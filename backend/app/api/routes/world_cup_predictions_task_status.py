@router.get("/auto-tune/status/{task_id}", response_model=FlexibleResponse)
async def get_auto_tune_status(task_id: str):
    """Get status of a background auto-tune task.

    Args:
        task_id: Task ID returned from auto-tune endpoint
    """
    from app.services.optimization_task_manager import get_task_manager

    task_manager = get_task_manager()
    task = await task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "status": "ok",
        "task": task.to_dict()
    }
