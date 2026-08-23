import pathlib

p = pathlib.Path("sports_aggregator/social/stories.py")
text = p.read_text(encoding="utf-8")

# ---------------------------------------------------------------- helpers --
old_canonical = text[text.index("def _canonical_url"):text.index("def _tokens")]
new_canonical = '''#: Query parameters that identify *which* resource a URL points at. Stripping the
#: whole query string collapsed every YouTube video onto "youtube.com/watch",
#: which then clustered 87 unrelated videos into a single story.
IDENTIFYING_PARAMS = ("v", "id", "story", "article", "p", "video_id", "watch")

#: Tracking parameters that never identify a resource and should not split one.
TRACKING_PREFIXES = ("utm_", "fb", "gcl", "ig_", "mc_")
TRACKING_PARAMS = {"s", "t", "ref", "source", "cmp", "campaign", "sh", "si",
                   "feature", "app", "spm", "at_medium", "at_campaign"}

#: Hosts whose links are the platform's own permalink rather than an article.
#: A self-link says "this is where the post lives", not "this is the story".
PLATFORM_HOSTS = {
    "bluesky": ("bsky.app",),
    "reddit": ("reddit.com", "redd.it"),
    "youtube": ("youtube.com", "youtu.be"),
    "podcast": (),
}


def _canonical_url(url: str) -> str:
    """Normalize a URL for comparison, preserving what identifies the resource.

    Tracking parameters are dropped so the same article shared twice matches;
    identifying parameters are kept so two different resources never collide.
    """
    if not url:
        return ""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if not host:
        return ""
    path = parsed.path.rstrip("/") or "/"
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.casefold()
        if lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES):
            continue
        if lowered in IDENTIFYING_PARAMS or host.endswith("youtube.com"):
            kept.append((lowered, value))
    query = urlencode(sorted(kept))
    return urlunsplit((parsed.scheme.casefold() or "https", host, path, query, ""))


def _external_article_url(item: dict) -> str:
    """The outside article a item points at, if it points at one at all.

    A platform permalink is not a story key. Two Bluesky posts both linking to
    their own bsky.app URLs are not the same story, and clustering on that put
    unrelated items together while leaving genuinely duplicated coverage apart.
    """
    canonical = _canonical_url(item.get("original_url") or "")
    if not canonical:
        return ""
    host = urlsplit(canonical).hostname or ""
    for platform_host in PLATFORM_HOSTS.get(item.get("platform") or "", ()):
        if host == platform_host or host.endswith("." + platform_host):
            return ""
    return canonical


'''
text = text.replace(old_canonical, new_canonical, 1)

text = text.replace(
    "from urllib.parse import urlsplit, urlunsplit",
    "from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit")

# Topics added since this list was written were silently typed as OTHER.
text = text.replace(
    '''               "NIL","MEDIA","ROSTER")''',
    '''               "NIL","MEDIA","ROSTER","DISCIPLINE","BOWL","SCHEDULE","OFFSEASON",
               "SEASON_PREVIEW","BETTING","COMMENTARY","FACILITIES")''')

# -------------------------------------------------------------- clustering --
start = text.index("            clusters=[]; by_url=defaultdict(list); remaining=[]")
end = text.index('            connection.execute("DELETE FROM story_items")')
text = text[:start] + '''            clusters = _build_clusters(items)

''' + text[end:]

# The role vocabulary changed; these names no longer exist in the data.
text = text.replace(
    '''                topics=set().union(*(item["topics"] for item in group)); candidates=[item for item in group
                    if item["source_role"]=="REPORTING_UNDETERMINED" and item["reliability_score"]>=4]''',
    '''                topics=set().union(*(item["topics"] for item in group))
                # Role names were renamed when role determination was rebuilt;
                # this list matched nothing, so no story ever chose a reporting
                # primary or marked an original-report candidate.
                candidates=[item for item in group
                    if item["source_role"] in REPORTING_ROLES and item["reliability_score"]>=4]''')
text = text.replace(
    '''                    elif item["source_role"]=="REPORTING_UNDETERMINED": role="CORROBORATION_CANDIDATE"; role_conf=.6''',
    '''                    elif item["source_role"] in REPORTING_ROLES: role="CORROBORATION_CANDIDATE"; role_conf=.6''')
text = text.replace(
    '''                primary=(candidates or official or group)[0]; confidence=(0.9 if method=="EXACT_EXTERNAL_URL" and len(group)>1''',
    '''                primary=(candidates or official or group)[0]; confidence=(0.9 if method=="SHARED_ARTICLE" and len(group)>1''')

# ------------------------------------------------------------- new builder --
anchor = "class StoryRepository:"
builder = '''#: Roles that represent journalism, for primary-source selection.
REPORTING_ROLES = {"ORIGINAL_REPORT", "REPORTING", "CORROBORATION"}

#: How similar two items' words must be to be treated as the same story.
#: Applied together with a shared entity and a shared topic, never alone.
SIMILARITY_THRESHOLD = 0.42

#: A shared resolved game or player is much stronger evidence than a shared
#: team, so it earns a lower wording bar.
STRONG_ENTITY_THRESHOLD = 0.28

#: Items further apart than this are separate stories even if they read alike.
CLUSTER_WINDOW_HOURS = 72

#: Above this many members a cluster has almost certainly over-merged. Capping
#: keeps one bad match from swallowing a feed the way the URL bug did.
MAX_CLUSTER_SIZE = 12


def _similarity(item: dict, anchor: dict) -> tuple[bool, str] | tuple[bool, None]:
    """Whether two items are the same story, and on what evidence."""
    try:
        gap = abs((datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
                   - datetime.fromisoformat(anchor["published_at"].replace("Z", "+00:00"))
                   ).total_seconds()) / 3600
    except (ValueError, KeyError):
        return False, None
    if gap > CLUSTER_WINDOW_HOURS:
        return False, None
    shared_games = item["games"] & anchor["games"]
    shared_players = item["players"] & anchor["players"]
    shared_teams = item["teams"] & anchor["teams"]
    if not (shared_games or shared_players or shared_teams):
        return False, None
    if not (item["topics"] & anchor["topics"]):
        return False, None
    overlap = _jaccard(item["tokens"], anchor["tokens"])
    if shared_games or shared_players:
        if overlap >= STRONG_ENTITY_THRESHOLD:
            return True, "SHARED_SUBJECT"
    if shared_teams and overlap >= SIMILARITY_THRESHOLD:
        return True, "SHARED_TEAM_TOPIC"
    return False, None


def _build_clusters(items: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """Group content into stories.

    Two passes. First, items pointing at the same outside article are the same
    story by definition. Then every remaining item is compared against every
    cluster -- including the article-seeded ones, which the previous version
    never did, so a report and its pickup could not merge unless neither carried
    a link.

    Matching takes the *best* candidate rather than the first, because the first
    acceptable anchor is an accident of ordering.
    """
    clusters: list[tuple[str, str, list[dict]]] = []
    by_article: dict[str, list[dict]] = defaultdict(list)
    loose: list[dict] = []
    for item in items:
        url = _external_article_url(item)
        (by_article[url] if url else loose).append(item)
    for url, group in by_article.items():
        clusters.append((f"url:{url}", "SHARED_ARTICLE", group))

    for item in loose:
        best_index, best_score, best_method = None, 0.0, None
        for index, (_, _, group) in enumerate(clusters):
            if len(group) >= MAX_CLUSTER_SIZE:
                continue
            matched, method = _similarity(item, group[0])
            if not matched:
                continue
            score = _jaccard(item["tokens"], group[0]["tokens"])
            if score > best_score:
                best_index, best_score, best_method = index, score, method
        if best_index is None:
            digest = hashlib.sha256(
                f"{item['platform']}:{item['platform_content_id']}".encode()).hexdigest()[:24]
            clusters.append((f"item:{digest}", "SINGLE_ITEM", [item]))
        else:
            key, method, group = clusters[best_index]
            group.append(item)
            # A cluster seeded by an article keeps that provenance; one grown
            # from similarity records how it actually formed.
            clusters[best_index] = (key, method if method == "SHARED_ARTICLE"
                                    else best_method or method, group)
    return clusters


class StoryRepository:'''
text = text.replace(anchor, builder, 1)
p.write_text(text, encoding="utf-8")
print("clustering rewritten")
