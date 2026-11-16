import httpx

from backend.config import settings


class Api:
    def __init__(self, user: str):
        self.headers = {"X-API-Key": settings().identities[user]}

    def request(self, method: str, path: str, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        response = httpx.request(
            method, settings().api_url + path, headers=headers, timeout=45, **kwargs
        )
        response.raise_for_status()
        return response

    def get(self, path: str, **params):
        return self.request("GET", path, params=params).json()

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs).json()
