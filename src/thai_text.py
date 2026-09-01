"""Cleanup for Thai copy on its way to a voice, a subtitle or a title.

Thai scripts on this channel are full of Latin script -- brand names, people,
loanwords -- and the pipeline used to whitelist the Thai block and delete
everything else. That shipped "ของ  มีจุดเริ่มต้นที่ซับซ้อน" to the channel
with Mark Zuckerberg and AI cut out of it, and burned subtitles with the same
holes in them. The only script actually worth removing is CJK, which the
models occasionally hallucinate into Thai output.
"""

import re

# Chinese, Japanese and Korean blocks, including halfwidth kana.
_CJK = re.compile(
    r'[⺀-〿぀-ヿ㄀-ㄯ㄰-㆏'
    r'㐀-䶿一-鿿가-힯豈-﫿･-ﾟ]'
)


def clean_thai(text: str) -> str:
    """Drop CJK hallucinations and collapse the gaps they leave behind."""
    return re.sub(r'\s{2,}', ' ', _CJK.sub('', text)).strip()
