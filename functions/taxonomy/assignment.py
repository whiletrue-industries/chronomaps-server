import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


def assign_topics(embeddings, reference_embeddings, similarity_threshold=0.35, max_tags=3):
    ref_keys = list(reference_embeddings.keys())
    ref_matrix = np.array([reference_embeddings[k] for k in ref_keys])

    similarities = cosine_similarity(embeddings, ref_matrix)

    assignments = []
    for i in range(len(embeddings)):
        sims = similarities[i]
        ranked = sorted(enumerate(sims), key=lambda x: -x[1])

        topics = []
        best_sim = ranked[0][1] if ranked else 0.0
        for rank, (idx, sim) in enumerate(ranked):
            if rank == 0 or (sim >= similarity_threshold and rank < max_tags):
                theme_slug, sub_theme_slug = ref_keys[idx]
                topics.append(f'{theme_slug}/{sub_theme_slug}')
            elif rank >= max_tags:
                break
        assignments.append(dict(
            topics=topics,
            best_similarity=round(float(best_sim), 4),
        ))
    return assignments
