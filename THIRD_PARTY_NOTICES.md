# Avisos de terceiros

O painel local distribui, dentro de `src/osa/panel/static/`, arquivos de cinco
pacotes de código aberto: dois com licença MIT (ícones e animações) e três
famílias de fonte com a SIL Open Font License 1.1. O texto completo de cada
licença está abaixo. Versões e hashes de integridade do registro npm estão
fixados em `src/osa/panel/static/MANIFEST.json`.

A extração de documentos (TASK-105) usa ainda pacotes Python que o `uv sync`
instala a partir do PyPI; eles não ficam no repositório. A seção "Dependências
Python da extração de documentos" lista os principais, com a licença declarada
por cada pacote; o texto completo de cada licença acompanha o pacote instalado
(`*.dist-info/licenses`).

## Phosphor Icons

- Pacote: `@phosphor-icons/core` versão **2.1.1**
- Fonte: https://github.com/phosphor-icons/core
- Integridade (registro npm):
  `sha512-v4ARvrip4qBCImOE5rmPUylOEK4iiED9ZyKjcvzuezqMaiRASCHKcRIuvvxL/twvLpkfnEODCOJp5dM4eZilxQ==`
- Uso: somente os arquivos SVG realmente usados nas telas, em
  `src/osa/panel/static/icons/`. A marca do cabeçalho não é Phosphor.

```text
MIT License

Copyright (c) 2023 Phosphor Icons

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## lottie-web

- Pacote: `lottie-web` versão **5.13.0**
- Fonte: https://github.com/airbnb/lottie-web
- Integridade (registro npm):
  `sha512-+gfBXl6sxXMPe8tKQm7qzLnUy5DUPJPKIyRHwtpCpyUEYjHYRJC/5gjUvdkuO2c3JllrPtHXH5UJJK8LRYl5yQ==`
- Uso: um único arquivo, `build/player/lottie_light.min.js` (renderizador SVG
  sem o motor de expressões), copiado sem alteração para
  `src/osa/panel/static/vendor/lottie_light.min.js`.

```text
The MIT License (MIT)

Copyright (c) 2015 Bodymovin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## IBM Plex Sans

- Pacote: `@ibm/plex-sans` versão **1.1.0** (pacote oficial da IBM no registro npm;
  fonte versão 3.005)
- Projeto original: https://github.com/IBM/plex
- Integridade (registro npm):
  `sha512-WPgvO6Yfj2w5YbhyAr1tv95RUz4LRJlqN+CmYvBglabXteufP1D1E9BABMde+ZIKdRbFJDoKF5eQzfhpnbgZcQ==`
- Uso: texto, controles e interface do painel. Seis arquivos WOFF2 estáticos, um por
  peso usado (400, 500 e 600; a IBM não publica versão variável para a web) e por recorte:
  `ibm-plex-sans-{400,500,600}-latin1.woff2` (de `fonts/split/woff2/IBMPlexSans-{Regular,
  Medium,SemiBold}-Latin1.woff2`) e `ibm-plex-sans-{400,500,600}-latin2.woff2` (idem,
  `-Latin2`). Os arquivos `latin2` (latino estendido) só são baixados quando uma página usa
  um caractere dessa faixa. Em `src/osa/panel/static/fonts/`.
- Licença: SIL Open Font License 1.1, no `LICENSE.txt` do pacote (reproduzido abaixo; o
  mesmo texto do projeto original). Linha de copyright gravada nas fontes:
  `Copyright 2018 IBM Corp. All rights reserved.`. A cópia que acompanha as fontes está em
  `src/osa/panel/static/fonts/OFL-ibm-plex-sans.txt` (o `LICENSE.txt` do pacote, byte a byte).
- Modificação: nenhuma; arquivos produzidos e publicados pelo detentor do copyright. São
  os arquivos web que a própria IBM gera e publica no pacote (já em WOFF2 e recortados
  por ela em faixas de caracteres), copiados byte a byte; só o nome do arquivo local é
  outro. Não são "Modified Versions" de terceiros, por isso as tabelas `name` internas
  dizem "IBM Plex Sans", como a IBM as gravou.
- Reserved Font Name: a licença declara Reserved Font Name "Plex"
  (`Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"`). A restrição vale para
  versões modificadas, que aqui não há. Mesmo assim o CSS do painel declara e pede só os
  apelidos neutros "Panel Sans" e "Panel Sans Fallback", que não colidem com uma fonte
  instalada na máquina.

```text
Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"

This Font Software is licensed under the SIL Open Font License, Version 1.1.

This license is copied below, and is also available with a FAQ at: http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

## IBM Plex Serif

- Pacote: `@ibm/plex-serif` versão **2.0.0** (pacote oficial da IBM no registro npm;
  fonte versão 3.006)
- Projeto original: https://github.com/IBM/plex
- Integridade (registro npm):
  `sha512-xVu9JsC18tBoQF2M6fofpsWrHj5a94saxlOZbYmXoC0E3UfjgS1JlibgrnaUJjnkLlEyCpMgI2PdhkDxijw0gA==`
- Uso: títulos de página, títulos de cartão e números da página inicial (peso 600).
  Dois arquivos WOFF2, `ibm-plex-serif-600-latin1.woff2` e `ibm-plex-serif-600-latin2.woff2`
  (de `fonts/split/woff2/IBMPlexSerif-SemiBold-Latin1.woff2` e `-Latin2.woff2`; o segundo só
  quando a página usa a faixa latina estendida). Em `src/osa/panel/static/fonts/`.
- Licença: SIL Open Font License 1.1, no `LICENSE.txt` do pacote (reproduzido abaixo; o
  mesmo texto do projeto original). Linha de copyright gravada nas fontes:
  `Copyright 2020 IBM Corp. All rights reserved.`. A cópia que acompanha as fontes está em
  `src/osa/panel/static/fonts/OFL-ibm-plex-serif.txt` (o `LICENSE.txt` do pacote, byte a byte).
- Modificação: nenhuma; arquivos produzidos e publicados pelo detentor do copyright. São
  os arquivos web que a própria IBM gera e publica no pacote (já em WOFF2 e recortados
  por ela em faixas de caracteres), copiados byte a byte; só o nome do arquivo local é
  outro. Não são "Modified Versions" de terceiros, por isso as tabelas `name` internas
  dizem "IBM Plex Serif", como a IBM as gravou.
- Reserved Font Name: a licença declara Reserved Font Name "Plex"
  (`Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"`). A restrição vale para
  versões modificadas, que aqui não há. Mesmo assim o CSS do painel declara e pede só os
  apelidos neutros "Panel Serif" e "Panel Serif Fallback", que não colidem com uma fonte
  instalada na máquina.

```text
Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"

This Font Software is licensed under the SIL Open Font License, Version 1.1.

This license is copied below, and is also available with a FAQ at: http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

## IBM Plex Mono

- Pacote: `@ibm/plex-mono` versão **2.5.0** (pacote oficial da IBM no registro npm;
  fonte versão 2.005)
- Projeto original: https://github.com/IBM/plex
- Integridade (registro npm):
  `sha512-STBJIPxPomOYPmBMO7z5TKPJUotAF9u3gAUumTqVgwgrAO+K4FRNh0MlhsoJjKhJKsMbBJR10/bk4inkj/wc1w==`
- Uso: pastas, códigos e endereços exibidos em fonte monoespaçada. Um arquivo WOFF2,
  `ibm-plex-mono-400-latin1.woff2` (peso 400, de
  `fonts/split/woff2/IBMPlexMono-Regular-Latin1.woff2`). Em `src/osa/panel/static/fonts/`.
- Licença: SIL Open Font License 1.1, no `LICENSE.txt` do pacote (reproduzido abaixo; o
  mesmo texto do projeto original). Linha de copyright gravada nas fontes:
  `Copyright 2017 IBM Corp. All rights reserved.`. A cópia que acompanha as fontes está em
  `src/osa/panel/static/fonts/OFL-ibm-plex-mono.txt` (o `LICENSE.txt` do pacote, byte a byte).
- Modificação: nenhuma; arquivos produzidos e publicados pelo detentor do copyright. São
  os arquivos web que a própria IBM gera e publica no pacote (já em WOFF2 e recortados
  por ela em faixas de caracteres), copiados byte a byte; só o nome do arquivo local é
  outro. Não são "Modified Versions" de terceiros, por isso as tabelas `name` internas
  dizem "IBM Plex Mono", como a IBM as gravou.
- Reserved Font Name: a licença declara Reserved Font Name "Plex"
  (`Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"`). A restrição vale para
  versões modificadas, que aqui não há. Mesmo assim o CSS do painel declara e pede só os
  apelidos neutros "Panel Mono" e "Panel Mono Fallback", que não colidem com uma fonte
  instalada na máquina.

```text
Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"

This Font Software is licensed under the SIL Open Font License, Version 1.1.

This license is copied below, and is also available with a FAQ at: http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

## Dependências Python da extração de documentos

Versões fixadas em `uv.lock`. Licença conforme os metadados e os arquivos de licença de
cada pacote. A escolha de cada um está em `docs/adr/0010-pdf-e-ocr-locais.md`.

| Pacote | Versão | Licença | Uso |
|---|---|---|---|
| pypdfium2 | 5.14.0 | BSD-3-Clause ou Apache-2.0; o binário do PDFium traz as licenças próprias em `licenses/data/*/BUILD_LICENSES` | texto e imagem de páginas de PDF |
| python-docx | 1.2.0 | MIT | DOCX |
| openpyxl (e et-xmlfile 2.0.0) | 3.1.5 | MIT | XLSX |
| lxml | 6.1.3 | BSD-3-Clause (libxml2 e libxslt sob MIT) | análise de XML (via python-docx) |
| defusedxml | 0.7.1 | Python Software Foundation License | recusa DTD e entidades em XML de OOXML |
| Pillow | 13.0.0 | MIT-CMU (HPND) | decodificação de imagens |
| pi-heif | 1.4.0 | código BSD-3-Clause; a wheel embute libheif e libde265, ambas LGPL-3.0 | decodificação de HEIC |
| RapidOCR | 3.10.0 | Apache-2.0; os modelos ONNX da wheel (PaddleOCR PP-OCRv6, direitos da Baidu e dos autores do PaddleOCR) também são Apache-2.0, ver `MODEL_LICENSES.md` no pacote | OCR local |
| onnxruntime | 1.31.0 | MIT | execução dos modelos do OCR |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 (a wheel embute bibliotecas de terceiros listadas em `LICENSE-3RD-PARTY.txt`) | pré-processamento do OCR |
| numpy, shapely, pyclipper, omegaconf, protobuf, flatbuffers, colorlog, six, tqdm, requests e dependências | ver `uv.lock` | BSD-3-Clause, MIT, Apache-2.0; shapely embute o GEOS (LGPL-2.1) e tqdm é MPL-2.0 e MIT | dependências do RapidOCR e do onnxruntime |

Atenção à LGPL: libheif e libde265 (em pi-heif) e GEOS (em shapely) são bibliotecas
dinâmicas distribuídas dentro das wheels, sem modificação, e podem ser trocadas pelo
usuário ao reinstalar o pacote. O projeto não as incorpora ao próprio código.

Só desenvolvimento, nunca instalado em produção: **reportlab** 5.0.1 (BSD) gera os PDFs das
fixtures de teste, e **pillow-heif** 1.8.1 (código BSD-3-Clause; as wheels embutem o
codificador x265, GPLv2) escreve a fixture HEIC. Nenhum dos dois entra em
`dependencies` do `pyproject.toml`, e nada do que geram é código deles.

## Conteúdo próprio

As animações em `src/osa/panel/static/lottie/*.json` são geradas por
`tools/lottie/generate.py` a partir de formas básicas e pertencem ao projeto;
não vêm de nenhuma biblioteca ou coleção de terceiros. O mesmo vale para a marca
do painel (escudo desenhado à mão, no cabeçalho de cada página) e para o
`favicon.svg`, que repete essa marca.
