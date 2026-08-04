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
    MAX_TO_PLOT: int = 500
    SIDE: int = 1000
    TAG: str = None
    LOCAL: bool = False
    V_OFFSET: bool = False
    ADD_TITLE: bool = True

    OPENAI_KEY: str = None
    CHRONOMAPS_API_URL: str = None

    def __post_init__(self):
        # Cells are wider than they are tall (or vice versa) - this is the height/width
        # ratio of the grid that keeps the map's overall proportions at OUT_RATIO.
        self.GRID_ASPECT = self.ORIGINAL_IMAGE_SIZE[0] * self.CELL_RATIOS[0] * self.OUT_RATIO / (self.ORIGINAL_IMAGE_SIZE[1] * self.CELL_RATIOS[1])
        self.TO_PLOT = self.MAX_TO_PLOT
        self._set_out_dim(self.OUT_DIM_X)

    def _set_out_dim(self, out_dim_x):
        self.OUT_DIM_X = out_dim_x
        self.OUT_DIM_Y = max(1, int(round(out_dim_x * self.GRID_ASPECT)))
        self.OUT_DIM = (self.OUT_DIM_X, self.OUT_DIM_Y)

    def fit_grid_to_records(self, num_records):
        """Size the grid to hold num_records at the configured density (FILL_RATIO),
        keeping the map's proportions. The grid always has at least one cell per record."""
        target_cells = max(num_records, 1) / self.FILL_RATIO
        start = max(1, int(round(math.sqrt(target_cells / self.GRID_ASPECT))))
        # Both dimensions are rounded to whole cells, so the width closest to the ideal
        # isn't necessarily the one that lands closest to the target density - look around it.
        candidates = []
        for out_dim_x in range(max(1, start - 2), start + 3):
            out_dim_y = max(1, int(round(out_dim_x * self.GRID_ASPECT)))
            cells = out_dim_x * out_dim_y
            if cells >= num_records:
                candidates.append((abs(cells - target_cells), out_dim_x))
        self._set_out_dim(min(candidates)[1] if candidates else start)
        while self.OUT_DIM_X * self.OUT_DIM_Y < num_records:
            self._set_out_dim(self.OUT_DIM_X + 1)
        return self.OUT_DIM

    def __str__(self):
        return f"TSNEParams(TAG={self.TAG}," +\
               f"ORIGINAL_IMAGE_SIZE={self.ORIGINAL_IMAGE_SIZE}, CELL_RATIOS={self.CELL_RATIOS}, " +\
               f"BG_COLOR={self.BG_COLOR}, OUT_DIM={self.OUT_DIM}, OUT_RATIO={self.OUT_RATIO}, PADDING_RATIO={self.PADDING_RATIO}, " +\
               f"FILL_RATIO={self.FILL_RATIO}, MAX_TO_PLOT={self.MAX_TO_PLOT}, SIDE={self.SIDE}, V_OFFSET={self.V_OFFSET})"
