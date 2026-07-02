from celery import shared_task


@shared_task(name="core.smoke_test")
def smoke_test(message: str = "ok") -> dict[str, str]:
    return {"status": "ok", "message": message}
