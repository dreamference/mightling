from app.users import get_usr


def handle(user_id):
    name = get_usr(user_id)
    return {"name": name} if name else {"error": "not found"}
