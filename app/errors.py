"""Kode error baku PRD §9."""


class CrawlError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def labeled(self) -> str:
        return f"[{self.code}] {self}"