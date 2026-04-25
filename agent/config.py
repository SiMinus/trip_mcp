from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    openai_model: str = "qwen-plus"
    amap_api_key: str = ""
    baidu_map_ak: str = ""
    tencent_map_key: str = ""
    redis_url: str = ""
    poi_cache_ttl_seconds: int = 1800
    qweather_api_key: str = ""
    chroma_persist_dir: str = "./data/chroma"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
