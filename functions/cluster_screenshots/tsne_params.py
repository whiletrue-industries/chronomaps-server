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
    SIDE: int = 1000
    TAG: str = None
    LOCAL: bool = False

    OPENAI_KEY: str = None
    CHRONOMAPS_API_URL: str = None

    def __post_init__(self):
        self.OUT_DIM_Y = int(math.ceil(self.OUT_DIM_X * self.ORIGINAL_IMAGE_SIZE[0] * self.CELL_RATIOS[0] * self.OUT_RATIO / (self.ORIGINAL_IMAGE_SIZE[1] * self.CELL_RATIOS[1])))
        self.OUT_DIM = (self.OUT_DIM_X, self.OUT_DIM_Y)
        self.TO_PLOT = int(self.OUT_DIM_X * self.OUT_DIM_Y * 0.75)
