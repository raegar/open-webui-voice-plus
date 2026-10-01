"""Make upstream's pipeline filters honour the chat's filter toggles.

Run at image build time against the base image's routers/pipelines.py. Upstream
applies every pipeline filter to every chat, so the Memory toggle in Integrations had
no effect on the persistent memory pipeline: it recorded every chat whatever the
toggle said. After this patch a pipeline filter whose model row is marked toggleable
(meta.toggle) only runs when the chat's selected filter_ids include it. Our
middleware puts filter_ids on the inlet payload, and the browser sends them with the
completed-chat request that drives the outlet.

Fails the build if upstream's layout has moved, rather than silently recording again.
"""

import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/open_webui/routers/pipelines.py"

source = open(PATH, encoding="utf-8").read()

CALL = "sorted_filters = get_sorted_filters(model_id, models)"
if source.count(CALL) != 2:
    sys.exit(f"pipelines.py: expected 2 calls to get_sorted_filters, found {source.count(CALL)}")
source = source.replace(
    CALL, "sorted_filters = get_sorted_filters(model_id, models, payload.get('filter_ids'))"
)

source += '''


# --- open-webui-voice-plus: filter toggles for pipeline filters ---------------------
_upstream_get_sorted_filters = get_sorted_filters


def _is_toggleable_pipeline(pipeline_id):
    """Whether this pipeline's model row is marked toggleable (meta.toggle)."""
    from open_webui.models.models import Models

    model = Models.get_model_by_id(pipeline_id)
    meta = getattr(model, 'meta', None) if model else None
    return bool(getattr(meta, 'toggle', False)) if meta else False


def get_sorted_filters(model_id, models, filter_ids=None):
    """Upstream's filters, minus toggleable ones the chat has not switched on.

    filter_ids of None means the caller sent no selection at all (an API client),
    which keeps upstream behaviour and runs everything.
    """
    filters = _upstream_get_sorted_filters(model_id, models)
    if filter_ids is None:
        return filters
    return [f for f in filters if not _is_toggleable_pipeline(f['id']) or f['id'] in filter_ids]
'''

open(PATH, "w", encoding="utf-8").write(source)
print("pipelines.py patched: toggleable pipeline filters follow filter_ids")
