import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from taxonomy.naming import generate_slug


def assign_topics(embeddings, sub_theme_centroids, theme_names, sub_theme_names,
                  similarity_threshold=0.35, max_tags=3):
    centroid_keys = list(sub_theme_centroids.keys())
    centroid_matrix = np.array([sub_theme_centroids[k] for k in centroid_keys])

    similarities = cosine_similarity(embeddings, centroid_matrix)

    assignments = []
    for i in range(len(embeddings)):
        sims = similarities[i]
        ranked = sorted(enumerate(sims), key=lambda x: -x[1])

        topics = []
        for rank, (idx, sim) in enumerate(ranked):
            if rank == 0 or (sim >= similarity_threshold and rank < max_tags):
                theme_id, sub_theme_id = centroid_keys[idx]
                theme_slug = generate_slug(theme_names[theme_id])
                sub_theme_slug = generate_slug(sub_theme_names[(theme_id, sub_theme_id)])
                topics.append(f'{theme_slug}/{sub_theme_slug}')
            elif rank >= max_tags:
                break
        assignments.append(topics)
    return assignments
