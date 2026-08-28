import math
from dataclasses import dataclass


@dataclass
class TSNEParams():
    EMBEDDING_DIMENSION: int = 3072
    PERPLEXITY: int = 50
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

    OPENAI_KEY: str = None
    CHRONOMAPS_API_URL: str = None

    def __post_init__(self):
        self.OUT_DIM_Y = int(round(self.OUT_DIM_X * self.ORIGINAL_IMAGE_SIZE[0] * self.CELL_RATIOS[0] * self.OUT_RATIO / (self.ORIGINAL_IMAGE_SIZE[1] * self.CELL_RATIOS[1])))
        self.OUT_DIM = (self.OUT_DIM_X, self.OUT_DIM_Y)
        self.TO_PLOT = int(self.OUT_DIM_X * self.OUT_DIM_Y * self.FILL_RATIO)

    def __str__(self):
        return f"TSNEParams(TAG={self.TAG}," +\
               f"ORIGINAL_IMAGE_SIZE={self.ORIGINAL_IMAGE_SIZE}, CELL_RATIOS={self.CELL_RATIOS}, " +\
               f"BG_COLOR={self.BG_COLOR}, OUT_DIM_X={self.OUT_DIM_X}, OUT_RATIO={self.OUT_RATIO}, PADDING_RATIO={self.PADDING_RATIO}, " +\
               f"FILL_RATIO={self.FILL_RATIO}, SIDE={self.SIDE}, V_OFFSET={self.V_OFFSET})"