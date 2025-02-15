import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

import torch
import torch.nn as nn
from torch.utils.data.dataset import Dataset
from tqdm import tqdm

import flair
import flair.embeddings
from flair.data import Dictionary, Sentence
from flair.datasets import DataLoader, FlairDatapointDataset
from flair.training_utils import MetricRegression, Result, store_embeddings

log = logging.getLogger("flair")


class TextRegressor(flair.nn.Model[Sentence]):
    def __init__(
        self,
        document_embeddings: flair.embeddings.DocumentEmbeddings,
        label_name: str = "label",
    ):

        super().__init__()
        log.info("Using REGRESSION - experimental")

        self.document_embeddings: flair.embeddings.DocumentEmbeddings = document_embeddings
        self.label_name = label_name

        self.decoder = nn.Linear(self.document_embeddings.embedding_length, 1)

        nn.init.xavier_uniform_(self.decoder.weight)

        self.loss_function = nn.MSELoss(reduction="sum")

        # auto-spawn on GPU if available
        self.to(flair.device)

    @property
    def label_type(self):
        return self.label_name

    def _prepare_tensors(self, sentences: List[Sentence]) -> Tuple[torch.Tensor]:
        self.document_embeddings.embed(sentences)
        embedding_names = self.document_embeddings.get_names()
        text_embedding_list = [sentence.get_embedding(embedding_names).unsqueeze(0) for sentence in sentences]
        text_embedding_tensor = torch.cat(text_embedding_list, 0).to(flair.device)
        return (text_embedding_tensor,)

    def forward(self, *args: torch.Tensor) -> torch.Tensor:
        (text_embedding_tensor,) = args
        label_scores = self.decoder(text_embedding_tensor)
        return label_scores

    def forward_loss(self, sentences: List[Sentence]) -> Tuple[torch.Tensor, int]:

        labels = self._labels_to_tensor(sentences)
        text_embedding_tensor = self._prepare_tensors(sentences)
        scores = self.forward(*text_embedding_tensor)

        return self.loss_function(scores.squeeze(1), labels), len(sentences)

    def _labels_to_tensor(self, sentences: List[Sentence]):
        indices = [
            torch.tensor([float(label.value) for label in sentence.labels], dtype=torch.float) for sentence in sentences
        ]

        vec = torch.cat(indices, 0).to(flair.device)

        return vec

    def predict(
        self,
        sentences: Union[Sentence, List[Sentence]],
        mini_batch_size: int = 32,
        verbose: bool = False,
        label_name: Optional[str] = None,
        embedding_storage_mode="none",
    ) -> List[Sentence]:

        if label_name is None:
            label_name = self.label_name if self.label_name is not None else "label"

        with torch.no_grad():
            if not isinstance(sentences, list):
                sentences = [sentences]

            if not sentences:
                return sentences
            filtered_sentences = self._filter_empty_sentences(sentences)
            reordered_sentences = sorted(filtered_sentences, key=lambda s: len(s), reverse=True)

            if len(reordered_sentences) == 0:
                return sentences

            dataloader = DataLoader(
                dataset=FlairDatapointDataset(reordered_sentences),
                batch_size=mini_batch_size,
            )
            # progress bar for verbosity
            if verbose:
                progress_bar = tqdm(dataloader)
                progress_bar.set_description("Batch inference")
                dataloader = progress_bar

            for batch in dataloader:
                # stop if all sentences are empty
                if not batch:
                    continue

                (sentence_tensor,) = self._prepare_tensors(batch)
                scores = self.forward(sentence_tensor)

                for (sentence, score) in zip(batch, scores.tolist()):
                    sentence.set_label(label_name, value=str(score[0]))

                # clearing token embeddings to save memory
                store_embeddings(batch, storage_mode=embedding_storage_mode)

            return sentences

    def forward_labels_and_loss(self, sentences: List[Sentence]) -> Tuple[torch.Tensor, torch.Tensor]:
        labels = self._labels_to_tensor(sentences)
        text_embedding_tensor = self._prepare_tensors(sentences)
        scores = self.forward(*text_embedding_tensor)

        return scores, self.loss_function(scores.squeeze(1), labels)

    def evaluate(
        self,
        data_points: Union[List[Sentence], Dataset],
        gold_label_type: str,
        out_path: Union[str, Path] = None,
        embedding_storage_mode: str = "none",
        mini_batch_size: int = 32,
        num_workers: Optional[int] = 8,
        main_evaluation_metric: Tuple[str, str] = ("micro avg", "f1-score"),
        exclude_labels: List[str] = [],
        gold_label_dictionary: Optional[Dictionary] = None,
        return_loss: bool = True,
        **kwargs,
    ) -> Result:

        # read Dataset into data loader, if list of sentences passed, make Dataset first
        if not isinstance(data_points, Dataset):
            data_points = FlairDatapointDataset(data_points)
        data_loader = DataLoader(data_points, batch_size=mini_batch_size, num_workers=num_workers)

        with torch.no_grad():
            eval_loss = torch.zeros(1, device=flair.device)

            metric = MetricRegression("Evaluation")

            lines: List[str] = []
            total_count = 0
            for batch in data_loader:

                if isinstance(batch, Sentence):
                    batch = [batch]

                scores, loss = self.forward_labels_and_loss(batch)

                true_values = []
                for sentence in batch:
                    total_count += 1
                    for label in sentence.get_labels(gold_label_type):
                        true_values.append(float(label.value))

                results = scores[:, 0].cpu().tolist()

                eval_loss += loss

                metric.true.extend(true_values)
                metric.pred.extend(results)

                for sentence, prediction, true_value in zip(batch, results, true_values):
                    eval_line = "{}\t{}\t{}\n".format(sentence.to_original_text(), true_value, prediction)
                    lines.append(eval_line)

                store_embeddings(batch, embedding_storage_mode)

            eval_loss /= total_count

            # TODO: not saving lines yet
            if out_path is not None:
                with open(out_path, "w", encoding="utf-8") as outfile:
                    outfile.write("".join(lines))
            log_line = f"{metric.mean_squared_error()}\t{metric.spearmanr()}" f"\t{metric.pearsonr()}"
            log_header = "MSE\tSPEARMAN\tPEARSON"

            detailed_result = (
                f"AVG: mse: {metric.mean_squared_error():.4f} - "
                f"mae: {metric.mean_absolute_error():.4f} - "
                f"pearson: {metric.pearsonr():.4f} - "
                f"spearman: {metric.spearmanr():.4f}"
            )

            result: Result = Result(
                main_score=metric.pearsonr(),
                loss=eval_loss.item(),
                log_header=log_header,
                log_line=log_line,
                detailed_results=detailed_result,
            )

            return result

    def _get_state_dict(self):
        model_state = {
            **super()._get_state_dict(),
            "document_embeddings": self.document_embeddings,
            "label_name": self.label_type,
        }
        return model_state

    @classmethod
    def _init_model_with_state_dict(cls, state, **kwargs):
        return super()._init_model_with_state_dict(
            state, document_embeddings=state.get("document_embeddings"), label_name=state.get("label_name"), **kwargs
        )

    @staticmethod
    def _filter_empty_sentences(sentences: List[Sentence]) -> List[Sentence]:
        filtered_sentences = [sentence for sentence in sentences if sentence.tokens]
        if len(sentences) != len(filtered_sentences):
            log.warning("Ignore {} sentence(s) with no tokens.".format(len(sentences) - len(filtered_sentences)))
        return filtered_sentences
