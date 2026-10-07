"""The multilingual embedding candidates (the user's decision of 2026-10-07: all major languages), with
the prefixes each model card prescribes. Models fastembed does not list are registered from the ONNX
export in their own repository (or Xenova's, for the int8 variants). Excluded, with the reason in the spec:
jina-embeddings-v3 (CC BY-NC 4.0), embeddinggemma-300m (Gemma terms), gte-multilingual-base (no ONNX
export; custom code)."""
from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

# name: (document prefix, query prefix, licence, custom registration or None)
CANDIDATES = {
    "minishlab/potion-multilingual-128M": ("", "", "MIT", None),
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": ("", "", "Apache-2.0", None),
    "intfloat/multilingual-e5-small": ("passage: ", "query: ", "MIT",
                                       dict(pooling=PoolingType.MEAN, hf="intfloat/multilingual-e5-small",
                                            dim=384, model_file="onnx/model.onnx")),
    "intfloat/multilingual-e5-small-int8": ("passage: ", "query: ", "MIT",
                                            dict(pooling=PoolingType.MEAN, hf="Xenova/multilingual-e5-small",
                                                 dim=384, model_file="onnx/model_int8.onnx")),
    "intfloat/multilingual-e5-base": ("passage: ", "query: ", "MIT",
                                      dict(pooling=PoolingType.MEAN, hf="intfloat/multilingual-e5-base",
                                           dim=768, model_file="onnx/model.onnx")),
    "ibm-granite/granite-embedding-107m-multilingual": ("", "", "Apache-2.0",
                                                        dict(pooling=PoolingType.CLS,
                                                             hf="ibm-granite/granite-embedding-107m-multilingual",
                                                             dim=384, model_file="model.onnx")),
    "Snowflake/snowflake-arctic-embed-m-v2.0": ("", "query: ", "Apache-2.0",
                                                dict(pooling=PoolingType.CLS,
                                                     hf="Snowflake/snowflake-arctic-embed-m-v2.0",
                                                     dim=768, model_file="onnx/model.onnx")),
    "Snowflake/snowflake-arctic-embed-m-v2.0-int8": ("", "query: ", "Apache-2.0",
                                                     dict(pooling=PoolingType.CLS,
                                                          hf="Snowflake/snowflake-arctic-embed-m-v2.0",
                                                          dim=768, model_file="onnx/model_int8.onnx")),
    "BAAI/bge-m3": ("", "", "MIT", dict(pooling=PoolingType.CLS, hf="BAAI/bge-m3", dim=1024,
                                        model_file="onnx/model.onnx", additional_files=["onnx/model.onnx_data"])),
    "Qwen/Qwen3-Embedding-0.6B-Q": ("", "Instruct: Given a web search query, retrieve relevant passages that "
                                        "answer the query\nQuery:", "Apache-2.0", None),
}
_done = set()


def register(name):
    custom = CANDIDATES[name][3]
    if custom and name not in _done:
        TextEmbedding.add_custom_model(model=name, pooling=custom["pooling"], normalization=True,
                                       sources=ModelSource(hf=custom["hf"]), dim=custom["dim"],
                                       model_file=custom["model_file"],
                                       additional_files=custom.get("additional_files"))
        _done.add(name)
