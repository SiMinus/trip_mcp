"""旅游知识库 MCP Server — 基于 ChromaDB 向量检索"""

import os
import chromadb
from sentence_transformers import SentenceTransformer
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("knowledge")

PERSIST_DIR = os.environ.get("CHROMA_PERSIST_DIR", "./data/chroma")
MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None
_embedder: SentenceTransformer | None = None


def _init():
    global _client, _collection, _embedder
    if _client is None:
        _embedder = SentenceTransformer(MODEL_NAME)
        _client = chromadb.PersistentClient(path=PERSIST_DIR)
        _collection = _client.get_or_create_collection(
            "tourism_knowledge",
            metadata={"hnsw:space": "cosine"},
        )


def _embed(texts: list[str]) -> list[list[float]]:
    _init()
    return _embedder.encode(texts, normalize_embeddings=True).tolist()


@mcp.tool()
def search_knowledge(query: str, top_k: int = 3) -> str:
    """从旅游知识库中检索与 query 相关的文化、历史、攻略信息。
    用于获取景点背景故事、美食文化、旅行贴士等。
    """
    _init()
    if _collection.count() == 0:
        return "知识库为空，请先运行 scripts/init_knowledge.py 初始化"

    results = _collection.query(
        query_embeddings=_embed([query]),
        n_results=min(top_k, _collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    lines = []
    for doc, meta, dist in zip(docs, metas, dists):
        score = 1 - dist  # cosine similarity
        source = meta.get("source", "")
        lines.append(f"[相关度 {score:.2f} | {source}]\n{doc}")
    return "\n\n---\n\n".join(lines) if lines else "未找到相关知识"


@mcp.tool()
def add_knowledge(text: str, source: str = "用户补充") -> str:
    """向知识库中添加一条旅游知识/攻略"""
    _init()
    import hashlib

    doc_id = hashlib.md5(text.encode()).hexdigest()[:12]
    _collection.upsert(
        ids=[doc_id],
        documents=[text],
        embeddings=_embed([text]),
        metadatas=[{"source": source}],
    )
    return f"已添加知识（ID: {doc_id}）"


if __name__ == "__main__":
    mcp.run()
