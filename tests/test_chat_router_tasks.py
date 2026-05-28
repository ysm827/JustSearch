import asyncio

from backend.app.routers import chat as chat_router


def test_cancel_and_drain_tasks_cancels_pending_tasks_and_observes_exceptions():
    async def run():
        cleanup_ran = asyncio.Event()

        async def pending_worker():
            try:
                await asyncio.sleep(60)
            finally:
                cleanup_ran.set()

        async def failing_worker():
            raise RuntimeError("worker failed")

        pending_task = asyncio.create_task(pending_worker())
        failing_task = asyncio.create_task(failing_worker())
        await asyncio.sleep(0)

        results = await chat_router._cancel_and_drain_tasks([pending_task, failing_task])

        assert cleanup_ran.is_set()
        assert pending_task.cancelled()
        assert any(isinstance(result, asyncio.CancelledError) for result in results)
        assert any(isinstance(result, RuntimeError) for result in results)

    asyncio.run(run())


def test_cancel_and_drain_tasks_tolerates_empty_task_list():
    assert asyncio.run(chat_router._cancel_and_drain_tasks([])) == []
