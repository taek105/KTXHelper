"""Backward-compatible imports for code that still references service.srt."""

from service.ktx import (
    KTX,
    KTX_STATIONS,
    LOGIN_WAIT_TIMEOUT,
    SCHEDULE_RESULT_SELECTOR,
    WAITING_QUEUE_POLL_FREQUENCY,
    WAITING_QUEUE_TIMEOUT,
    build_search_url,
    get_schedule,
    get_schedule_page_state,
    install_chromedriver,
    is_waiting_queue_visible,
    slow_send_keys,
    wait_for_waiting_queue,
)

SRT = KTX

__all__ = [
    "KTX",
    "KTX_STATIONS",
    "LOGIN_WAIT_TIMEOUT",
    "SCHEDULE_RESULT_SELECTOR",
    "SRT",
    "WAITING_QUEUE_POLL_FREQUENCY",
    "WAITING_QUEUE_TIMEOUT",
    "build_search_url",
    "get_schedule",
    "get_schedule_page_state",
    "install_chromedriver",
    "is_waiting_queue_visible",
    "slow_send_keys",
    "wait_for_waiting_queue",
]
