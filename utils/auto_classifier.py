"""
Auto-Classifier fuer Job Families.

KI-basierte Erkennung von Job Families aus vorhandenen Job-Titeln
mittels TF-IDF Vektorisierung und Hierarchischem Clustering.
"""

import re
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

# Scikit-learn imports
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


# =============================================================================
# KONFIGURATION
# =============================================================================

@dataclass
class ClusteringConfig:
    """Konfiguration fuer Auto-Erkennung."""

    # Minimale Cluster-Groesse (Jobs pro Family)
    min_cluster_size: int = 3

    # Maximale Anzahl Families
    max_families: int = 20

    # Aehnlichkeitsschwelle fuer Clustering (Distance Threshold)
    distance_threshold: float = 1.2

    # Stoppwoerter (nicht fuer Clustering verwenden)
    stopwords: List[str] = field(default_factory=lambda: [
        "der", "die", "das", "und", "in", "für", "fuer", "mit", "bei",
        "m/w/d", "m/w", "(m/w/d)", "(m/w)", "w/m/d", "w/m",
        "senior", "junior", "jr", "sr",
        "gmbh", "ag", "kg", "ohg",
        "teilzeit", "vollzeit", "minijob",
        "befristet", "unbefristet"
    ])

    # Wichtige Begriffe (werden hoeher gewichtet)
    important_terms: List[str] = field(default_factory=lambda: [
        "leiter", "berater", "sachbearbeiter", "administrator",
        "entwickler", "manager", "direktor", "vorstand",
        "controller", "analyst", "spezialist", "experte",
        "assistent", "sekretär", "techniker", "ingenieur"
    ])


# =============================================================================
# TEXT-VORVERARBEITUNG
# =============================================================================

def preprocess_job_title(title: str, config: ClusteringConfig = None) -> str:
    """
    Bereitet einen Job-Titel fuer die Analyse vor.

    Args:
        title: Original Job-Titel
        config: Clustering-Konfiguration

    Returns:
        Bereinigter Job-Titel
    """
    if config is None:
        config = ClusteringConfig()

    if not isinstance(title, str):
        return ""

    # Lowercase
    text = title.lower()

    # Entferne Sonderzeichen (behalte Bindestriche und Umlaute)
    text = re.sub(r'[^\w\säöüß\-]', ' ', text)

    # Entferne Stoppwoerter
    words = text.split()
    words = [w for w in words if w not in config.stopwords and len(w) > 1]

    return ' '.join(words)


def extract_keywords(job_titles: List[str], top_n: int = 10) -> List[Tuple[str, int]]:
    """
    Extrahiert die haeufigsten Woerter aus einer Liste von Job-Titeln.

    Args:
        job_titles: Liste von Job-Titeln
        top_n: Anzahl der Top-Woerter

    Returns:
        Liste von (Wort, Haeufigkeit) Tupeln
    """
    config = ClusteringConfig()
    all_words = []

    for title in job_titles:
        cleaned = preprocess_job_title(title, config)
        all_words.extend(cleaned.split())

    # Zaehle Woerter
    word_counts = Counter(all_words)

    return word_counts.most_common(top_n)


# =============================================================================
# CLUSTERING
# =============================================================================

def analyze_job_titles(
    df: pd.DataFrame,
    column: str = "Planstelle",
    config: ClusteringConfig = None
) -> Dict:
    """
    Analysiert Job-Titel und schlaegt Gruppierungen vor.

    Algorithmus:
    1. TF-IDF Vektorisierung der Job-Titel
    2. Hierarchisches Clustering
    3. Cluster-Labels als Family-Vorschlaege
    4. Pattern-Extraktion pro Cluster

    Args:
        df: DataFrame mit Job-Daten
        column: Spaltenname mit Job-Titeln
        config: Clustering-Konfiguration

    Returns:
        {
            "suggested_families": [
                {
                    "name": "Kundenberatung",
                    "jobs": ["Kundenberater Privat", ...],
                    "suggested_patterns": ["*Kundenberater*"],
                    "confidence": 0.85,
                    "job_count": 25
                }
            ],
            "unclustered": ["Einzelposition 1", ...],
            "statistics": {
                "total_jobs": 500,
                "unique_titles": 120,
                "clustered": 450,
                "clustering_rate": 0.9
            }
        }
    """
    if not SKLEARN_AVAILABLE:
        return {
            "error": "scikit-learn nicht installiert. Bitte 'pip install scikit-learn' ausfuehren.",
            "suggested_families": [],
            "unclustered": [],
            "statistics": {"total_jobs": 0, "unique_titles": 0, "clustered": 0, "clustering_rate": 0}
        }

    if config is None:
        config = ClusteringConfig()

    if column not in df.columns:
        return {
            "error": f"Spalte '{column}' nicht gefunden",
            "suggested_families": [],
            "unclustered": [],
            "statistics": {"total_jobs": 0, "unique_titles": 0, "clustered": 0, "clustering_rate": 0}
        }

    # Unique Job-Titel extrahieren
    job_titles = df[column].dropna().unique().tolist()
    total_jobs = len(df[column].dropna())
    unique_titles = len(job_titles)

    if unique_titles < config.min_cluster_size:
        return {
            "error": f"Zu wenige unique Job-Titel ({unique_titles}). Mindestens {config.min_cluster_size} benoetigt.",
            "suggested_families": [],
            "unclustered": job_titles,
            "statistics": {
                "total_jobs": total_jobs,
                "unique_titles": unique_titles,
                "clustered": 0,
                "clustering_rate": 0
            }
        }

    # Vorverarbeitung
    processed_titles = [preprocess_job_title(t, config) for t in job_titles]

    # Filtere leere Titel
    valid_indices = [i for i, t in enumerate(processed_titles) if t.strip()]
    valid_titles = [processed_titles[i] for i in valid_indices]
    original_titles = [job_titles[i] for i in valid_indices]

    if len(valid_titles) < config.min_cluster_size:
        return {
            "error": "Zu wenige valide Job-Titel nach Vorverarbeitung",
            "suggested_families": [],
            "unclustered": job_titles,
            "statistics": {
                "total_jobs": total_jobs,
                "unique_titles": unique_titles,
                "clustered": 0,
                "clustering_rate": 0
            }
        }

    # TF-IDF Vektorisierung
    vectorizer = TfidfVectorizer(
        max_features=500,
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.95
    )

    try:
        tfidf_matrix = vectorizer.fit_transform(valid_titles)
    except ValueError as e:
        return {
            "error": f"TF-IDF Fehler: {e}",
            "suggested_families": [],
            "unclustered": job_titles,
            "statistics": {
                "total_jobs": total_jobs,
                "unique_titles": unique_titles,
                "clustered": 0,
                "clustering_rate": 0
            }
        }

    # Hierarchisches Clustering
    # Berechne optimale Anzahl Cluster (max_families begrenzt)
    n_clusters = min(config.max_families, max(2, unique_titles // config.min_cluster_size))

    clustering = AgglomerativeClustering(
        n_clusters=n_clusters,
        metric='euclidean',
        linkage='ward'
    )

    try:
        cluster_labels = clustering.fit_predict(tfidf_matrix.toarray())
    except Exception as e:
        return {
            "error": f"Clustering Fehler: {e}",
            "suggested_families": [],
            "unclustered": job_titles,
            "statistics": {
                "total_jobs": total_jobs,
                "unique_titles": unique_titles,
                "clustered": 0,
                "clustering_rate": 0
            }
        }

    # Gruppiere Titel nach Cluster
    clusters = {}
    for i, label in enumerate(cluster_labels):
        if label not in clusters:
            clusters[label] = []
        clusters[label].append(original_titles[i])

    # Erstelle Family-Vorschlaege
    suggested_families = []
    unclustered = []

    for cluster_id, titles in clusters.items():
        if len(titles) < config.min_cluster_size:
            # Zu kleine Cluster -> unclustered
            unclustered.extend(titles)
            continue

        # Family-Name vorschlagen
        suggested_name = suggest_family_name(titles)

        # Patterns extrahieren
        patterns = extract_common_patterns(titles)

        # Konfidenz berechnen (basierend auf Cluster-Groesse und Kohaerenz)
        confidence = calculate_cluster_confidence(titles, tfidf_matrix, valid_indices, cluster_labels, cluster_id)

        suggested_families.append({
            "name": suggested_name,
            "jobs": titles,
            "suggested_patterns": patterns,
            "confidence": confidence,
            "job_count": len(titles),
            "cluster_id": int(cluster_id)
        })

    # Sortiere nach Groesse (absteigend)
    suggested_families.sort(key=lambda x: x["job_count"], reverse=True)

    # Statistiken
    clustered_count = sum(f["job_count"] for f in suggested_families)
    clustering_rate = clustered_count / unique_titles if unique_titles > 0 else 0

    return {
        "suggested_families": suggested_families,
        "unclustered": unclustered,
        "statistics": {
            "total_jobs": total_jobs,
            "unique_titles": unique_titles,
            "clustered": clustered_count,
            "unclustered_count": len(unclustered),
            "clustering_rate": clustering_rate,
            "family_count": len(suggested_families)
        }
    }


def calculate_cluster_confidence(
    titles: List[str],
    tfidf_matrix,
    valid_indices: List[int],
    cluster_labels: np.ndarray,
    cluster_id: int
) -> float:
    """
    Berechnet die Konfidenz eines Clusters basierend auf interner Kohaerenz.

    Args:
        titles: Job-Titel im Cluster
        tfidf_matrix: TF-IDF Matrix
        valid_indices: Gueltige Indizes
        cluster_labels: Cluster-Labels
        cluster_id: ID des Clusters

    Returns:
        Konfidenz-Wert zwischen 0 und 1
    """
    # Finde Indizes der Cluster-Mitglieder in der TF-IDF Matrix
    cluster_indices = [i for i, label in enumerate(cluster_labels) if label == cluster_id]

    if len(cluster_indices) < 2:
        return 0.5

    # Berechne paarweise Aehnlichkeit
    cluster_vectors = tfidf_matrix[cluster_indices].toarray()
    similarities = cosine_similarity(cluster_vectors)

    # Durchschnittliche Aehnlichkeit (ohne Diagonale)
    n = len(cluster_indices)
    total_sim = (similarities.sum() - n) / (n * (n - 1)) if n > 1 else 0

    # Normalisiere auf 0-1 und skaliere
    confidence = min(1.0, max(0.3, total_sim + 0.3))

    # Bonus fuer groessere Cluster
    size_bonus = min(0.2, len(titles) / 50)
    confidence = min(1.0, confidence + size_bonus)

    return round(confidence, 2)


# =============================================================================
# PATTERN-EXTRAKTION
# =============================================================================

def extract_common_patterns(job_titles: List[str], max_patterns: int = 5) -> List[str]:
    """
    Extrahiert gemeinsame Patterns aus einer Liste von Job-Titeln.

    Args:
        job_titles: Liste von Job-Titeln
        max_patterns: Maximale Anzahl Patterns

    Returns:
        Liste von Pattern-Strings (mit Wildcards)
    """
    if not job_titles:
        return []

    config = ClusteringConfig()
    patterns = []

    # Extrahiere alle Woerter
    word_counts = Counter()
    for title in job_titles:
        words = preprocess_job_title(title, config).split()
        for word in words:
            if len(word) >= 3:  # Mindestens 3 Zeichen
                word_counts[word] += 1

    # Finde haeufige Woerter (in mindestens 30% der Titel)
    threshold = max(2, len(job_titles) * 0.3)
    common_words = [word for word, count in word_counts.items() if count >= threshold]

    # Sortiere nach Haeufigkeit
    common_words.sort(key=lambda w: word_counts[w], reverse=True)

    # Erstelle Patterns
    for word in common_words[:max_patterns]:
        # Pruefe ob Wort am Anfang, Ende oder in der Mitte vorkommt
        pattern = f"*{word}*"

        # Vermeide zu generische Patterns
        if len(word) >= 4 and pattern not in patterns:
            patterns.append(pattern)

    # Falls keine Patterns gefunden, erstelle aus erstem Wort
    if not patterns and job_titles:
        first_words = []
        for title in job_titles[:5]:
            words = preprocess_job_title(title, config).split()
            if words:
                first_words.append(words[0])

        if first_words:
            most_common = Counter(first_words).most_common(1)
            if most_common:
                patterns.append(f"*{most_common[0][0]}*")

    return patterns[:max_patterns]


def suggest_family_name(job_titles: List[str]) -> str:
    """
    Schlaegt einen Family-Namen basierend auf haeufigen Woertern vor.

    Args:
        job_titles: Liste von Job-Titeln

    Returns:
        Vorgeschlagener Family-Name
    """
    if not job_titles:
        return "Unbekannte Familie"

    config = ClusteringConfig()

    # Extrahiere und zaehle alle Woerter
    word_counts = Counter()
    for title in job_titles:
        words = preprocess_job_title(title, config).split()
        for word in words:
            if len(word) >= 3:
                word_counts[word] += 1

    if not word_counts:
        return "Unbekannte Familie"

    # Priorisiere wichtige Begriffe
    for term in config.important_terms:
        if term in word_counts and word_counts[term] >= len(job_titles) * 0.3:
            return term.capitalize()

    # Nimm haeufigstes Wort
    most_common = word_counts.most_common(2)

    if len(most_common) >= 2:
        # Kombiniere die zwei haeufigsten Woerter
        word1, word2 = most_common[0][0], most_common[1][0]
        return f"{word1.capitalize()} & {word2.capitalize()}"
    elif most_common:
        return most_common[0][0].capitalize()
    else:
        return "Unbekannte Familie"


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def convert_to_definitions(
    suggested_families: List[Dict],
    include_unclustered: bool = False,
    unclustered: List[str] = None
) -> Dict:
    """
    Konvertiert Clustering-Ergebnisse in v2-kompatible Job Family Definitionen.

    Args:
        suggested_families: Liste der vorgeschlagenen Families
        include_unclustered: Ob nicht-geclusterte Jobs als separate Family inkludiert werden sollen
        unclustered: Liste der nicht-geclusterten Jobs

    Returns:
        v2-kompatible Job Family Definitionen
    """
    definitions = {
        "jobfamilies": {},
        "manual_overrides": {},
        "metadata": {
            "version": "2.0",
            "source": "auto_classifier",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
    }

    for family in suggested_families:
        family_name = family["name"]

        # Stelle sicher, dass Name unique ist
        if family_name in definitions["jobfamilies"]:
            family_name = f"{family_name} ({family['cluster_id']})"

        definitions["jobfamilies"][family_name] = {
            "description": f"Automatisch erkannt ({family['job_count']} Jobs)",
            "patterns": family["suggested_patterns"],
            "min_qualification": "",
            "manual_assignments": [],
            "auto_confidence": family["confidence"]
        }

    # Optional: Nicht-geclusterte Jobs
    if include_unclustered and unclustered:
        definitions["jobfamilies"]["Sonstige (nicht klassifiziert)"] = {
            "description": f"Nicht automatisch zugeordnet ({len(unclustered)} Jobs)",
            "patterns": [],
            "min_qualification": "",
            "manual_assignments": [],
            "auto_confidence": 0.0
        }

    return definitions


def merge_similar_families(
    families: List[Dict],
    similarity_threshold: float = 0.7
) -> List[Dict]:
    """
    Fuehrt aehnliche Families zusammen.

    Args:
        families: Liste der Families
        similarity_threshold: Schwelle fuer Zusammenfuehrung

    Returns:
        Bereinigte Liste der Families
    """
    if not SKLEARN_AVAILABLE or len(families) < 2:
        return families

    # Erstelle Vektoren aus Family-Namen und Patterns
    config = ClusteringConfig()
    texts = []

    for f in families:
        combined = f["name"] + " " + " ".join(f.get("suggested_patterns", []))
        texts.append(preprocess_job_title(combined, config))

    vectorizer = TfidfVectorizer()
    try:
        tfidf_matrix = vectorizer.fit_transform(texts)
        similarities = cosine_similarity(tfidf_matrix)
    except Exception:
        return families

    # Finde Paare zum Zusammenfuehren
    merged_indices = set()
    result = []

    for i in range(len(families)):
        if i in merged_indices:
            continue

        merged_family = families[i].copy()
        merged_family["jobs"] = merged_family["jobs"].copy()

        for j in range(i + 1, len(families)):
            if j in merged_indices:
                continue

            if similarities[i, j] >= similarity_threshold:
                # Fuehre zusammen
                merged_family["jobs"].extend(families[j]["jobs"])
                merged_family["suggested_patterns"] = list(set(
                    merged_family.get("suggested_patterns", []) +
                    families[j].get("suggested_patterns", [])
                ))
                merged_family["job_count"] = len(merged_family["jobs"])
                merged_family["confidence"] = max(
                    merged_family.get("confidence", 0),
                    families[j].get("confidence", 0)
                )
                merged_indices.add(j)

        result.append(merged_family)

    return result
