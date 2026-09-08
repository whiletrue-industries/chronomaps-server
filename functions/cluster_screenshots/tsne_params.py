import math
from dataclasses import dataclass


@dataclass
class TSNEParams():
    EMBEDDING_DIMENSION: int = 3072
    # Perplexity is roughly how many neighbours each point attends to. It is
    # scaled with the record count (about a third of it, between MIN_PERPLEXITY
    # and PERPLEXITY): a fixed 50 capped at n-1 meant that a small set had every
    # point attending to every other, so t-SNE spread them out evenly and no
    # clusters could form.
    PERPLEXITY: int = 50
    MIN_PERPLEXITY: int = 5
    PERPLEXITY_FRACTION: float = 1 / 3
    TSNE_ITER: int = 5000
    ORIGINAL_IMAGE_SIZE: int = (530, 1000)
    CELL_RATIOS: int = (1.86, 1.135)
    BG_COLOR: tuple[int] = (255, 253, 246)
    OUT_DIM_X: int = 23
    OUT_RATIO: float = 1.0
    PADDING_RATIO: float = 0.5
    FILL_RATIO: float = 0.75
    SIDE: int = 1000
    TAG: str = None
    LOCAL: bool = False
    V_OFFSET: bool = False
    ADD_TITLE: bool = True
    # Max favorability backfills per workspace per run; the rest wait for the
    # next run rather than pushing the job past its request timeout.
    ANALYSIS_BACKFILL_LIMIT: int = 50
    # Below this many records t-SNE is meaningless (and perplexity collapses),
    # so the map is drawn as one block in the middle instead of skipped.
    MIN_TSNE_RECORDS: int = 10

    OPENAI_KEY: str = None
    CHRONOMAPS_API_URL: str = None

    def __post_init__(self):
        self.OUT_DIM_Y = int(round(self.OUT_DIM_X * self.ORIGINAL_IMAGE_SIZE[0] * self.CELL_RATIOS[0] * self.OUT_RATIO / (self.ORIGINAL_IMAGE_SIZE[1] * self.CELL_RATIOS[1])))
        self.OUT_DIM = (self.OUT_DIM_X, self.OUT_DIM_Y)
        self.TO_PLOT = int(self.OUT_DIM_X * self.OUT_DIM_Y * self.FILL_RATIO)

    def perplexity_for(self, num_records):
        """Perplexity for a set of num_records, always below the count (t-SNE requires it)."""
        scaled = int(num_records * self.PERPLEXITY_FRACTION)
        return max(1, min(max(self.MIN_PERPLEXITY, scaled), self.PERPLEXITY, num_records - 1))

    def __str__(self):
        return f"TSNEParams(TAG={self.TAG}," +\
               f"ORIGINAL_IMAGE_SIZE={self.ORIGINAL_IMAGE_SIZE}, CELL_RATIOS={self.CELL_RATIOS}, " +\
               f"BG_COLOR={self.BG_COLOR}, OUT_DIM_X={self.OUT_DIM_X}, OUT_RATIO={self.OUT_RATIO}, PADDING_RATIO={self.PADDING_RATIO}, " +\
               f"FILL_RATIO={self.FILL_RATIO}, SIDE={self.SIDE}, V_OFFSET={self.V_OFFSET})"