# MathJax

The story view sets maths with [MathJax](https://www.mathjax.org/) from these
files, so a document's maths is set without a network. They are the parts of
the npm packages `mathjax` and `@mathjax/<font>-font` (one per maths font in
`limelight.typography.MATHS_FONTS`) that TeX input with SVG output can load,
at the version `packaging/vendor_mathjax.py` names. That script writes this
folder; nothing here is edited by hand.

MathJax and its fonts are Copyright The MathJax Consortium and licensed under
the Apache License, Version 2.0, in `mathjax/LICENSE`.
