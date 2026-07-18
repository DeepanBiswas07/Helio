from collections import deque

# store last N messages
MAX_HISTORY = 6

conversation_history = deque(maxlen=MAX_HISTORY)


def add_message(role, content):
    conversation_history.append({
        "role": role,
        "content": content
    })


def get_context():
    context = ""

    for msg in conversation_history:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            context += f"User: {content}\n"
        else:
            context += f"Assistant: {content}\n"

    return context.strip()


def clear_memory():
    conversation_history.clear()