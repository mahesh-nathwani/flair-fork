from typing import Any, Dict, List, Optional, Type

import pytest
import torch

from flair.data import Sentence
from flair.embeddings import Embeddings


class BaseEmbeddingsTest:
    embedding_cls: Type[Embeddings[Sentence]]
    is_token_embedding: bool
    is_document_embedding: bool
    default_args: Dict[str, Any]
    valid_args: List[Dict[str, Any]] = []
    invalid_args: List[Dict[str, Any]] = []
    invalid_names: List[str] = []
    name_field: Optional[str] = None
    weired_texts: List[str] = [
        "Hybrid mesons , qq ̄ states with an admixture",
        "typical proportionalities of ∼ 1nmV − 1 [ 3,4 ] .",
        "🤟 🤟  🤟 hüllo",
        "🤟hallo 🤟 🤟 🤟 🤟",
        "🤟hallo 🤟 🤟 🤟 🤟",
        "🤟",
    ]

    def create_embedding_from_name(self, name: str):
        """Overwrite this method if it is more complex to load an embedding by name"""
        assert self.name_field is not None
        kwargs = dict(self.default_args)
        kwargs.pop(self.name_field)
        return self.embedding_cls(name, **kwargs)  # type: ignore

    def create_embedding_with_args(self, args: Dict[str, Any]):
        kwargs = dict(self.default_args)
        for k, v in args.items():
            kwargs[k] = v
        return self.embedding_cls(**kwargs)

    @pytest.mark.parametrize("text", weired_texts)
    def test_embedding_works_with_weird_text(self, text):
        embeddings = self.create_embedding_with_args(self.default_args)
        embedding_names = embeddings.get_names()
        sentence = Sentence(text)
        embeddings.embed(sentence)

        if self.is_token_embedding:
            for token in sentence:
                assert len(token.get_embedding(embedding_names)) == embeddings.embedding_length
        if self.is_document_embedding:
            assert len(sentence.get_embedding(embedding_names)) == embeddings.embedding_length

    @pytest.mark.parametrize("args", valid_args)
    def test_embedding_also_sets_trailing_whitespaces(self, args):
        if not self.is_token_embedding:
            pytest.skip("The test is only valid for token embeddings")
        embeddings = self.create_embedding_with_args(args)

        sentence: Sentence = Sentence(["hello", " ", "hm", " "])
        embeddings.embed(sentence)
        names = embeddings.get_names()
        for token in sentence:
            assert len(token.get_embedding(names)) == embeddings.embedding_length

    @pytest.mark.parametrize("args", valid_args)
    def test_generic_sentence(self, args):
        embeddings = self.create_embedding_with_args(args)

        sentence: Sentence = Sentence("I love Berlin")
        embeddings.embed(sentence)
        names = embeddings.get_names()
        if self.is_token_embedding:
            for token in sentence:
                assert len(token.get_embedding(names)) == embeddings.embedding_length
        if self.is_document_embedding:
            assert len(sentence.get_embedding(names)) == embeddings.embedding_length

    @pytest.mark.parametrize("name", invalid_names)
    def test_load_non_existing_embedding(self, name):
        with pytest.raises(ValueError):
            self.create_embedding_from_name(name)

    def test_keep_batch_order(self):
        embeddings = self.create_embedding_with_args(self.default_args)
        embedding_names = embeddings.get_names()

        sentences_1 = [Sentence("First sentence"), Sentence("This is second sentence")]
        sentences_2 = [Sentence("This is second sentence"), Sentence("First sentence")]

        embeddings.embed(sentences_1)
        embeddings.embed(sentences_2)

        assert sentences_1[0].to_original_text() == "First sentence"
        assert sentences_1[1].to_original_text() == "This is second sentence"

        if self.is_document_embedding:
            assert (
                torch.norm(
                    sentences_1[0].get_embedding(embedding_names) - sentences_2[1].get_embedding(embedding_names)
                )
                == 0.0
            )
            assert (
                torch.norm(
                    sentences_1[1].get_embedding(embedding_names) - sentences_2[0].get_embedding(embedding_names)
                )
                == 0.0
            )
        if self.is_token_embedding:
            for i in range(len(sentences_1[0])):
                assert (
                    torch.norm(
                        sentences_1[0][i].get_embedding(embedding_names)
                        - sentences_2[1][i].get_embedding(embedding_names)
                    )
                    == 0.0
                )
            for i in range(len(sentences_1[1])):
                assert (
                    torch.norm(
                        sentences_1[1][i].get_embedding(embedding_names)
                        - sentences_2[0][i].get_embedding(embedding_names)
                    )
                    == 0.0
                )
        del embeddings
