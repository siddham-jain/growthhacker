import html
import json
import zlib
from collections.abc import Sequence
from datetime import datetime
from string import Template

import numpy as np

from app.evaluation.packs.schema import FormField, PackItem, PackSchema


class PackExpired(RuntimeError):
    pass


def pack_order(item_ids: Sequence[str], seed: int, participant: str) -> list[str]:
    """a reproducible order per participant, so order effects differ between labellers."""
    rng = np.random.default_rng([seed, zlib.crc32(participant.encode())])
    return [item_ids[i] for i in rng.permutation(len(item_ids))]


def _field_html(item_id: str, field: FormField) -> str:
    name = html.escape(f"{item_id}::{field.name}")
    label = html.escape(field.label)
    if field.kind == "text":
        return f'<label class="f">{label}<textarea data-f="{name}"></textarea></label>'
    if field.kind == "scale":
        options = tuple(str(n) for n in range(1, 6))
    elif field.kind == "bool":
        options = ("true", "false")
    else:
        options = field.options
    input_type = "checkbox" if field.kind == "multi" else "radio"
    choices = "".join(
        f'<label class="o"><input type="{input_type}" data-f="{name}" name="{name}" '
        f'value="{html.escape(option)}">{html.escape(option.replace("_", " "))}</label>'
        for option in options
    )
    return f'<fieldset class="f"><legend>{label}</legend>{choices}</fieldset>'


_PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>$title</title>
<meta name="robots" content="noindex">
<style>
body{font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:980px;padding:24px;color:#1d1d1f}
header{border-bottom:1px solid #ddd;margin-bottom:16px}
.item{border:1px solid #ddd;border-radius:8px;padding:16px;margin:16px 0}
.f{margin:8px 0;border:0;padding:0}.o{margin-right:14px;white-space:nowrap}
textarea{display:block;width:100%;min-height:48px}
table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #e3e3e3;padding:2px 6px;text-align:left}
tr.target{background:#fff3c4;font-weight:600}
nav{position:sticky;bottom:0;background:#fff;padding:8px 0;border-top:1px solid #ddd}
.expired{color:#a00;font-weight:600}
</style></head><body>
<header><h1>$title</h1><p>Participant: <b>$participant</b> · generated $generated · expires $expires</p>
$header</header>
<noscript><p class="expired">This pack needs JavaScript to check its expiry and save your answers.</p></noscript>
<main id="pack" hidden>$preface$items</main>
<nav id="nav" hidden><button id="prev">Previous</button> <span id="page"></span>
<button id="next">Next</button> <button id="export">Export CSV</button> <span id="saved"></span></nav>
<script>
const META = $meta;
(function () {
  if (Date.now() >= Date.parse(META.expires_at)) {
    document.body.innerHTML = '<p class="expired">This pack has expired (data policy). Generate a new one.</p>';
    return;
  }
  const key = 'gh-pack:' + META.pack_id + ':' + META.participant;
  const state = JSON.parse(localStorage.getItem(key) || '{}');
  const items = Array.from(document.querySelectorAll('.item'));
  const pages = Math.max(1, Math.ceil(items.length / META.batch_size));
  let page = state.__page || 0, shownAt = Date.now();
  function save() { state.__page = page; localStorage.setItem(key, JSON.stringify(state));
    document.getElementById('saved').textContent = 'saved'; }
  function show() {
    items.forEach((el, i) => { el.hidden = Math.floor(i / META.batch_size) !== page; });
    document.getElementById('page').textContent = 'Batch ' + (page + 1) + ' of ' + pages;
    shownAt = Date.now(); window.scrollTo(0, 0);
  }
  document.querySelectorAll('[data-f]').forEach((input) => {
    const [id, field] = input.dataset.f.split('::');
    const answer = (state[id] = state[id] || {});
    const value = answer[field];
    if (input.type === 'checkbox') input.checked = (value || []).includes(input.value);
    else if (input.type === 'radio') input.checked = value === input.value;
    else if (value !== undefined) input.value = value;
    input.addEventListener('change', () => {
      if (input.type === 'checkbox') {
        const set = new Set(answer[field] || []);
        input.checked ? set.add(input.value) : set.delete(input.value);
        answer[field] = Array.from(set);
      } else { answer[field] = input.value; }
      answer.__seconds = (answer.__seconds || 0) + Math.round((Date.now() - shownAt) / 1000);
      shownAt = Date.now(); save();
    });
  });
  function csvCell(v) { v = v === undefined || v === null ? '' : String(v);
    return /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
  document.getElementById('export').addEventListener('click', () => {
    const rows = [META.columns.join(',')];
    META.order.forEach((id) => {
      const a = state[id] || {};
      rows.push(META.columns.map((c) => {
        if (c === META.id_column) return csvCell(id);
        if (c === 'seconds_spent') return csvCell(a.__seconds || '');
        const v = a[c]; return csvCell(Array.isArray(v) ? v.join(';') : v);
      }).join(','));
    });
    const blob = new Blob([rows.join('\\n') + '\\n'], {type: 'text/csv'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = META.kind + '-' + META.participant + '.csv';
    link.click();
  });
  document.getElementById('prev').onclick = () => { page = Math.max(0, page - 1); save(); show(); };
  document.getElementById('next').onclick = () => { page = Math.min(pages - 1, page + 1); save(); show(); };
  document.getElementById('pack').hidden = false;
  document.getElementById('nav').hidden = false;
  show();
})();
</script></body></html>
""")


class PackRenderer:
    """one self-contained html file per participant; answers live in localStorage until exported."""

    def __init__(self, schema: PackSchema, batch_size: int) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.schema = schema
        self.batch_size = batch_size

    def render(
        self,
        items: Sequence[PackItem],
        *,
        pack_id: str,
        participant: str,
        seed: int,
        generated_at: datetime,
        expires_at: datetime,
        preface_html: str = "",
    ) -> str:
        if generated_at >= expires_at:
            raise PackExpired("this pack has expired under the data policy; generate a new one")
        for item in items:
            leaked = set(item.visible) & self.schema.hidden_fields
            if leaked:
                raise ValueError(f"pack item {item.item_id} exposes hidden fields: {sorted(leaked)}")
        by_id = {item.item_id: item for item in items}
        order = pack_order([item.item_id for item in items], seed, participant)
        blocks = "".join(self._item(by_id[item_id], number) for number, item_id in enumerate(order, 1))
        meta = {
            "kind": self.schema.kind,
            "pack_id": pack_id,
            "participant": participant,
            "expires_at": expires_at.isoformat(),
            "batch_size": self.batch_size,
            "columns": list(self.schema.csv_columns),
            "id_column": self.schema.id_column,
            "order": order,
        }
        return _PAGE.substitute(
            title=html.escape(self.schema.title),
            participant=html.escape(participant),
            generated=html.escape(generated_at.isoformat(timespec="minutes")),
            expires=html.escape(expires_at.isoformat(timespec="minutes")),
            header=self.schema.header_html,
            preface=preface_html,
            items=blocks,
            meta=json.dumps(meta).replace("</", "<\\/"),
        )

    def _item(self, item: PackItem, number: int) -> str:
        fields = "".join(_field_html(item.item_id, field) for field in self.schema.fields)
        return (
            f'<section class="item" data-id="{html.escape(item.item_id)}"><h2>Item {number}</h2>'
            f"{item.body_html}<form>{fields}</form></section>"
        )
