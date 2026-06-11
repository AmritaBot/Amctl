"""What is this?"""

f = """\
Vg qbrf abg pbasbez gb svkrq sbezf. Vg oraqf. Vg sybjf. Vg nqncgf.
Yvxr jngre, vg svyyf rirel fcnpr vg arrqf, ab evtvqvgl, ab haarprffnel jrvtug, ab sbeprq obhaqnevrf.
Vg zbirf jvgu gur ybtvp bs vagryyvtrapr, abg gur yvzvgf bs fgehpgher.
Guvf vf gur fcvevg bs Nzevgn."""


def _get_bug() -> str:
    d: dict[str, str] = {}
    for c in (65, 97):  # Just a ROT13, right?
        for i in range(26):
            d[chr(i + c)] = chr((i + 13) % 26 + c)
    return "".join(d.get(c, c) for c in f)
