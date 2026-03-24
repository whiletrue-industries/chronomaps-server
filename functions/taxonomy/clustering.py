import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_distances


def build_hierarchy(embeddings, n_themes=None, min_sub_themes=2, max_sub_themes=6):
    n_items = len(embeddings)
    if n_themes is None:
        n_themes = max(5, min(15, n_items // 50))
    n_themes = min(n_themes, n_items)

    dist_matrix = cosine_distances(embeddings)

    level1 = AgglomerativeClustering(
        n_clusters=n_themes,
        metric='precomputed',
        linkage='average'
    )
    theme_labels = level1.fit_predict(dist_matrix)

    theme_centroids = {}
    sub_theme_labels = np.full(n_items, -1, dtype=int)
    sub_theme_centroids = {}
    sub_theme_global_id = 0

    for theme_id in sorted(set(theme_labels)):
        mask = theme_labels == theme_id
        theme_embeddings = embeddings[mask]
        theme_centroids[theme_id] = theme_embeddings.mean(axis=0)
        theme_indices = np.where(mask)[0]

        n_sub = max(min_sub_themes, min(max_sub_themes, len(theme_embeddings) // 10))
        n_sub = min(n_sub, len(theme_embeddings))

        if len(theme_embeddings) <= min_sub_themes:
            local_sub_labels = np.zeros(len(theme_embeddings), dtype=int)
        else:
            sub_dist = cosine_distances(theme_embeddings)
            level2 = AgglomerativeClustering(
                n_clusters=n_sub,
                metric='precomputed',
                linkage='average'
            )
            local_sub_labels = level2.fit_predict(sub_dist)

        for local_sub_id in sorted(set(local_sub_labels)):
            sub_mask = local_sub_labels == local_sub_id
            sub_embeddings = theme_embeddings[sub_mask]
            sub_theme_centroids[(theme_id, sub_theme_global_id)] = sub_embeddings.mean(axis=0)
            for idx in theme_indices[sub_mask]:
                sub_theme_labels[idx] = sub_theme_global_id
            sub_theme_global_id += 1

    return dict(
        theme_labels=theme_labels,
        sub_theme_labels=sub_theme_labels,
        theme_centroids=theme_centroids,
        sub_theme_centroids=sub_theme_centroids,
    )
