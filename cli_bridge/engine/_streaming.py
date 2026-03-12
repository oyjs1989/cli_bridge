"""Streaming utilities extracted from AgentLoop.

Contains:
- STREAMING_CHANNELS / buffer-size constants
- analyze_and_build_outbound() — builds OutboundMessage from iflow response text
- process_with_streaming() — runs a full streaming exchange and publishes updates
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from loguru import logger

from cli_bridge.bus import OutboundMessage
from cli_bridge.engine.analyzer import result_analyzer

if TYPE_CHECKING:
    from cli_bridge.bus import InboundMessage, MessageBus
    from cli_bridge.channels.manager import ChannelManager

# Channels that support streaming (edit-in-place) output
STREAMING_CHANNELS = {"telegram", "discord", "slack", "dingtalk", "qq", "feishu"}

# Streaming output buffer size range (characters)
STREAM_BUFFER_MIN = 10
STREAM_BUFFER_MAX = 25


def analyze_and_build_outbound(
    response: str,
    channel: str,
    chat_id: str,
    metadata: dict | None = None,
) -> OutboundMessage:
    """Analyze response with ResultAnalyzer and build OutboundMessage with media.

    Scans iflow output for generated file paths (image/audio/video/doc) and
    attaches them via OutboundMessage.media for file-callback capable channels.
    """
    analysis = result_analyzer.analyze({"output": response, "success": True})

    media_files: list[str] = []
    if analysis.image_files:
        media_files.extend(analysis.image_files)
        logger.info(f"Detected {len(analysis.image_files)} image(s) in response")
    if analysis.audio_files:
        media_files.extend(analysis.audio_files)
        logger.info(f"Detected {len(analysis.audio_files)} audio file(s) in response")
    if analysis.video_files:
        media_files.extend(analysis.video_files)
        logger.info(f"Detected {len(analysis.video_files)} video file(s) in response")
    if analysis.doc_files:
        media_files.extend(analysis.doc_files)
        logger.info(f"Detected {len(analysis.doc_files)} document(s) in response")

    if media_files:
        logger.info(f"File callback: attaching {len(media_files)} file(s) to outbound message")

    return OutboundMessage(
        channel=channel,
        chat_id=chat_id,
        content=response,
        media=media_files,
        metadata=metadata or {},
    )


async def process_with_streaming(
    adapter,
    bus: MessageBus,
    msg: InboundMessage,
    message_content: str,
    model: str,
    channel_manager: ChannelManager | None,
    stream_buffers: dict[str, str],
    buffer_min: int = STREAM_BUFFER_MIN,
    buffer_max: int = STREAM_BUFFER_MAX,
) -> str:
    """Process a message with streaming support and publish real-time updates.

    Uses a character-buffer mechanism: each time the buffer accumulates N chars
    (random in STREAM_BUFFER_MIN..STREAM_BUFFER_MAX), an intermediate update
    with _progress=True/_streaming=True is published to the bus.

    After the adapter returns the final response, publishes the final content
    followed by a _streaming_end=True termination signal (except for QQ/DingTalk
    which have channel-specific direct handling).

    Args:
        adapter: AI backend adapter (IFlowAdapter or compatible)
        bus: Message bus for publishing streaming updates
        msg: The inbound message being processed
        message_content: Fully prepared message content (with injected context)
        model: Model identifier to use
        channel_manager: Optional channel manager for direct channel access
        stream_buffers: Shared stream buffer dict (mutated in place by key)

    Returns:
        Final response text
    """
    session_key = f"{msg.channel}:{msg.chat_id}"

    # Initialise buffer
    stream_buffers[session_key] = ""

    # Unflushed character count and current per-chunk threshold
    unflushed_count = 0
    current_threshold = random.randint(buffer_min, buffer_max)

    # DingTalk: direct AI Card mode — create card immediately for instant reply
    dingtalk_channel = None
    if msg.channel == "dingtalk" and channel_manager:
        dingtalk_channel = channel_manager.get_channel("dingtalk")
        if dingtalk_channel and hasattr(dingtalk_channel, "start_streaming"):
            await dingtalk_channel.start_streaming(msg.chat_id)
        dingtalk_channel = channel_manager.get_channel("dingtalk")

    # QQ: segment-based direct sending (split at newlines)
    qq_channel = None
    qq_segment_buffer = ""      # content accumulating for current segment
    qq_line_buffer = ""          # incomplete line (no \n yet) — needed to detect ```
    qq_newline_count = 0
    qq_in_code_block = False     # newlines inside code blocks do not count toward threshold
    if msg.channel == "qq" and channel_manager:
        qq_channel = channel_manager.get_channel("qq")

    async def on_chunk(channel: str, chat_id: str, chunk_text: str):
        nonlocal \
            unflushed_count, \
            current_threshold, \
            qq_segment_buffer, \
            qq_line_buffer, \
            qq_newline_count, \
            qq_in_code_block

        key = f"{channel}:{chat_id}"

        # Update accumulated buffer (all channels — used for final content and logs)
        stream_buffers[key] = stream_buffers.get(key, "") + chunk_text

        # QQ: split by newline, bypass char-buffer logic
        if channel == "qq" and qq_channel:
            threshold = getattr(qq_channel.config, "split_threshold", 0)
            if threshold > 0:
                qq_line_buffer += chunk_text
                while "\n" in qq_line_buffer:
                    idx = qq_line_buffer.index("\n")
                    complete_line = qq_line_buffer[:idx]

                    qq_line_buffer = qq_line_buffer[idx + 1:]

                    # Track code-block state to ignore newlines inside blocks
                    if complete_line.strip().startswith("```"):
                        qq_in_code_block = not qq_in_code_block

                    qq_segment_buffer += complete_line + "\n"

                    if not qq_in_code_block:
                        qq_newline_count += 1
                        if qq_newline_count >= threshold:
                            segment = qq_segment_buffer.strip()
                            qq_segment_buffer = ""
                            qq_newline_count = 0
                            if segment:
                                await qq_channel.send(
                                    OutboundMessage(
                                        channel=channel,
                                        chat_id=chat_id,
                                        content=segment,
                                        metadata={
                                            "reply_to_id": msg.metadata.get("message_id")
                                        },
                                    )
                                )
                                from cli_bridge.session.recorder import get_recorder

                                recorder = get_recorder()
                                if recorder:
                                    recorder.record_outbound(
                                        OutboundMessage(
                                            channel=channel,
                                            chat_id=chat_id,
                                            content=segment,
                                            metadata={
                                                "reply_to_id": msg.metadata.get("message_id")
                                            },
                                        )
                                    )
            return  # QQ does not use char-buffer logic below

        unflushed_count += len(chunk_text)

        # Flush when enough chars have accumulated
        if unflushed_count >= current_threshold:
            unflushed_count = 0
            current_threshold = random.randint(buffer_min, buffer_max)

            # DingTalk: direct channel method
            if (
                channel == "dingtalk"
                and dingtalk_channel
                and hasattr(dingtalk_channel, "handle_streaming_chunk")
            ):
                await dingtalk_channel.handle_streaming_chunk(
                    chat_id, stream_buffers[key], is_final=False
                )
            else:
                # All other channels: via message bus
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=channel,
                        chat_id=chat_id,
                        content=stream_buffers[key],
                        metadata={
                            "_progress": True,
                            "_streaming": True,
                            "reply_to_id": msg.metadata.get("message_id"),
                        },
                    )
                )

    async def on_tool_call(channel: str, chat_id: str, tool_name: str) -> None:
        """Tool-call progress hint — inject into stream buffer so user sees activity."""
        await on_chunk(channel, chat_id, f"\n> 🔧 *{tool_name}*\n")

    try:
        # Run streaming chat
        response = await adapter.chat_stream(
            message=message_content,
            channel=msg.channel,
            chat_id=msg.chat_id,
            model=model,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
        )

        # Clean up buffer
        final_content = stream_buffers.pop(session_key, "")

        # Prefer adapter's clean final text (claude result event);
        # stream buffer may contain injected tool-call hint lines.
        display_final = response.strip() if response and response.strip() else final_content

        # QQ: drain remaining unsent buffer
        if msg.channel == "qq" and qq_channel:
            threshold = getattr(qq_channel.config, "split_threshold", 0)
            from cli_bridge.session.recorder import get_recorder

            recorder = get_recorder()
            if threshold <= 0:
                content_to_send = final_content.strip()
                if content_to_send:
                    await qq_channel.send(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=content_to_send,
                            metadata={"reply_to_id": msg.metadata.get("message_id")},
                        )
                    )
                    if recorder:
                        recorder.record_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=content_to_send,
                                metadata={"reply_to_id": msg.metadata.get("message_id")},
                            )
                        )
            else:
                remainder_to_send = (qq_segment_buffer + qq_line_buffer).strip()
                if remainder_to_send:
                    await qq_channel.send(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=remainder_to_send,
                            metadata={"reply_to_id": msg.metadata.get("message_id")},
                        )
                    )
                    if recorder:
                        recorder.record_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=remainder_to_send,
                                metadata={"reply_to_id": msg.metadata.get("message_id")},
                            )
                        )

        if display_final:
            # Scan final output for file attachments
            analysis = result_analyzer.analyze({"output": display_final, "success": True})
            media_files = (
                analysis.image_files
                + analysis.audio_files
                + analysis.video_files
                + analysis.doc_files
            )

            if media_files:
                logger.info(
                    f"Stream completed: detected {len(media_files)} file(s) for callback"
                )

            # DingTalk: final update via direct channel call
            if (
                msg.channel == "dingtalk"
                and dingtalk_channel
                and hasattr(dingtalk_channel, "handle_streaming_chunk")
            ):
                await dingtalk_channel.handle_streaming_chunk(
                    msg.chat_id, display_final, is_final=True
                )
                # Send detected files separately after DingTalk stream end
                if media_files:
                    await bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            media=media_files,
                        )
                    )
            elif msg.channel != "qq":
                # All other streaming channels: publish final content + end signal
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=display_final,
                        media=media_files,
                        metadata={
                            "_progress": True,
                            "_streaming": True,
                            "reply_to_id": msg.metadata.get("message_id"),
                        },
                    )
                )
                # Streaming-end termination signal
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="",
                        metadata={
                            "_streaming_end": True,
                            "reply_to_id": msg.metadata.get("message_id"),
                        },
                    )
                )
            logger.info(f"Streaming response completed for {msg.channel}:{msg.chat_id}")
        else:
            fallback = (
                "⚠️ 本轮未产出可见文本（可能会话上下文过长）。"
                "我已自动尝试恢复，如仍失败请发送 /new 开启新会话。"
            )
            await bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=fallback,
                    metadata={"reply_to_id": msg.metadata.get("message_id")},
                )
            )
            logger.warning(f"Streaming produced empty output for {msg.channel}:{msg.chat_id}")

        return display_final

    except Exception as e:
        # Clean up buffer on error
        stream_buffers.pop(session_key, None)
        raise e
