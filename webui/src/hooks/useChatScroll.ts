import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, TouchEvent, WheelEvent } from "react";

/** Follow new content only while pinned; reading history always takes priority. */
export function useChatScroll(messages: unknown[]) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastTop = useRef(0);
  const touchY = useRef<number | null>(null);
  const [showScrollButton, setShowScrollButton] = useState(false);

  const update = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    // Scrollbar movement may precede its scroll event during a render/resize.
    if (element.scrollTop < lastTop.current - 1 && element.scrollHeight - element.clientHeight - element.scrollTop > 1) {
      following.current = false;
    }
    if (following.current) element.scrollTop = element.scrollHeight;
    lastTop.current = element.scrollTop;
    setShowScrollButton(element.scrollHeight - element.clientHeight - element.scrollTop > 24);
  }, []);

  const reset = useCallback(() => {
    following.current = true;
    setShowScrollButton(false);
  }, []);

  const scrollToBottom = useCallback(() => {
    following.current = true;
    lastTop.current = scrollRef.current?.scrollTop ?? 0;
    update(); // Immediate, confined to the conversation; no in-flight smooth scroll.
  }, [update]);

  useLayoutEffect(update, [messages, update]);
  useLayoutEffect(() => {
    const element = scrollRef.current, content = contentRef.current;
    if (!element || !content) return;
    let frame = 0;
    // Cards, Markdown and the composer can resize without changing messages.
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    });
    observer.observe(element);
    observer.observe(content);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [update]);

  const onScroll = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    const remaining = element.scrollHeight - element.clientHeight - element.scrollTop;
    if (remaining <= 1) following.current = true;
    else if (element.scrollTop < lastTop.current - 1) following.current = false;
    lastTop.current = element.scrollTop;
    setShowScrollButton(remaining > 24);
  }, []);

  // Record intent synchronously, before another stream/resize can move the view.
  const onWheel = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0) following.current = false;
  }, []);
  const onTouchStart = useCallback((event: TouchEvent<HTMLDivElement>) => {
    touchY.current = event.touches[0]?.clientY ?? null;
  }, []);
  const onTouchMove = useCallback((event: TouchEvent<HTMLDivElement>) => {
    const next = event.touches[0]?.clientY ?? null;
    if (next !== null && touchY.current !== null && next > touchY.current) following.current = false;
    touchY.current = next;
  }, []);
  const onKeyDownCapture = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true]")) return;
    if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
      following.current = false;
    }
  }, []);
  const onClickCapture = useCallback((event: MouseEvent<HTMLDivElement>) => {
    // Opening an older process or tool result is an explicit request to read it.
    if ((event.target as HTMLElement).closest("button[aria-expanded]")) following.current = false;
  }, []);

  return { scrollRef, contentRef, showScrollButton, scrollToBottom, reset,
    scrollEvents: { onScroll, onWheel, onTouchStart, onTouchMove, onKeyDownCapture, onClickCapture } };
}
