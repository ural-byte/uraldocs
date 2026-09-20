"""Локальный совместимый сервер для проверки протокола, без генеративной модели."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def embeddings(payload: dict) -> dict:
    texts = payload.get("input")
    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
        raise ValueError("Поле input должно быть массивом строк")
    return {"data": [{"index": index, "embedding": [1.0, 0.0]} for index, _ in enumerate(texts)]}


def completion(payload: dict) -> dict:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        raise ValueError("Поле messages должно содержать сообщение пользователя")
    current = json.loads(messages[-1].get("content", ""))
    question = current.get("question", "").casefold()
    sources = current.get("sources", [])
    answer = {"insufficient": True, "citation_ids": []}
    for source in sources:
        excerpt = source.get("excerpt", "")
        if "лазур" in question and ("срок" in question or "когда" in question) and "12 мая 2027 года" in excerpt:
            answer = {"insufficient": False, "answer": "Срок подачи заявки — 12 мая 2027 года.", "citation_ids": [source["id"]]}
            break
        if "atlas" in question and ("day" in question or "when" in question) and "Tuesdays" in excerpt:
            answer = {"insufficient": False, "answer": "The help desk is open on Tuesdays.", "citation_ids": [source["id"]]}
            break
    return {"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_000_000:
                raise ValueError("Некорректный размер запроса")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ожидался JSON-объект")
            if self.path == "/v1/embeddings":
                result = embeddings(payload)
            elif self.path == "/v1/chat/completions":
                result = completion(payload)
            else:
                self.send_error(404)
                return
            status = 200
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            status, result = 400, {"error": str(error)}
        data = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
