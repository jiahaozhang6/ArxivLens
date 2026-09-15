import httpx
import pytest
import respx

from app.services.llm import (
    AnalysisPayload,
    _anthropic_models_endpoint,
    _openai_models_endpoint,
    _parse_model_options,
    build_arxiv_query,
    extract_json_object,
    list_available_models,
)


def test_extract_json_object_accepts_markdown_fence():
    result = extract_json_object(
        '\x60\x60\x60json\n{"summary":"ok","novelty_score":7}\n\x60\x60\x60'
    )
    assert result["summary"] == "ok"
    assert result["novelty_score"] == 7


def test_analysis_payload_normalizes_list_strings():
    payload = AnalysisPayload.model_validate(
        {
            "summary": "摘要",
            "research_question": "问题",
            "contributions": "- 贡献一\n- 贡献二",
            "methodology": "方法",
            "experiments": "实验",
            "limitations": ["限制"],
            "reading_advice": "建议",
            "relevance_reason": "相关",
            "keywords": "LLM\nRAG",
            "novelty_score": 8,
            "rigor_score": 7,
            "relevance_score": 9,
        }
    )
    assert payload.contributions == ["贡献一", "贡献二"]
    assert payload.keywords == ["LLM", "RAG"]


def test_analysis_payload_rejects_out_of_range_scores():
    with pytest.raises(ValueError):
        AnalysisPayload.model_validate(
            {
                "summary": "x",
                "research_question": "x",
                "methodology": "x",
                "experiments": "x",
                "reading_advice": "x",
                "relevance_reason": "x",
                "novelty_score": 12,
                "rigor_score": 5,
                "relevance_score": 5,
            }
        )


def test_build_arxiv_query_deduplicates_and_sanitizes_terms():
    result = build_arxiv_query(
        ["cs.AI", "cs.LG", "cs.AI"],
        ["large language models", 'AI "agents"', "Large Language Models"],
    )

    assert result == (
        '(cat:cs.AI OR cat:cs.LG) AND '
        '(all:"large language models" OR all:"AI agents")'
    )


def test_model_list_endpoints_accept_api_roots_and_completion_urls():
    assert _openai_models_endpoint("https://api.example.com/v1") == (
        "https://api.example.com/v1/models"
    )
    assert _openai_models_endpoint("https://api.example.com/v1/chat/completions") == (
        "https://api.example.com/v1/models"
    )
    assert _anthropic_models_endpoint("https://api.anthropic.com") == (
        "https://api.anthropic.com/v1/models"
    )
    assert _anthropic_models_endpoint("https://api.anthropic.com/v1/messages") == (
        "https://api.anthropic.com/v1/models"
    )


def test_parse_model_options_supports_common_shapes_and_deduplicates():
    assert _parse_model_options(
        {
            "data": [
                {"id": "model-b", "display_name": "Model B"},
                {"id": "model-a"},
                {"id": "model-a", "display_name": "Duplicate"},
            ]
        }
    ) == [
        {"id": "model-a", "label": "model-a"},
        {"id": "model-b", "label": "Model B"},
    ]
    assert _parse_model_options({"result": {"items": ["model-c"]}}) == [
        {"id": "model-c", "label": "model-c"}
    ]


@pytest.mark.asyncio
@respx.mock
async def test_list_available_models_uses_openai_compatible_auth():
    route = respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "model-a"}]})
    )

    models = await list_available_models(
        "openai_compatible", "https://api.example.com/v1", "secret-key"
    )

    assert models == [{"id": "model-a", "label": "model-a"}]
    assert route.calls[0].request.headers["authorization"] == "Bearer secret-key"


@pytest.mark.asyncio
@respx.mock
async def test_list_available_models_uses_anthropic_auth():
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": "claude-example", "display_name": "Claude Example"}]},
        )
    )

    models = await list_available_models(
        "anthropic", "https://api.anthropic.com", "secret-key"
    )

    assert models == [{"id": "claude-example", "label": "Claude Example"}]
    assert route.calls[0].request.headers["x-api-key"] == "secret-key"
    assert route.calls[0].request.headers["anthropic-version"] == "2023-06-01"
