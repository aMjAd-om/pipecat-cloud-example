async def get_customer_visits(context, arguments, *, repository):
    if context.customer is None:
        raise ValueError("customer context unavailable")
    return {"demo": context.demo, "source": context.source,
            "visits": await repository.recent_visits(context, arguments.limit)}
