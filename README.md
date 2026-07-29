# Arquivo histórico do fórum UniDev

Restauração estática, pesquisável e somente leitura do fórum brasileiro **UniDev**, cobrindo as mensagens publicadas entre **2000 e o encerramento do fórum**.

- Site: <https://skhaz.github.io/unidev/> — publicação suspensa enquanto o gate estiver bloqueado
- Repositório: <https://github.com/skhaz/unidev>
- Fontes primárias: inventários CDX do Internet Archive e índices do Common Crawl

> **Estado atual:** o acervo bruto verificado v3 está preservado em <https://github.com/skhaz/unidev/releases/tag/archive-data-v3>, mas a publicação do novo espelho continua bloqueada. A auditoria integral encontrou 58.444 variantes de páginas e 9.065 recursos referenciados sem captura ou origem válida; o arquivo não inventa seus conteúdos nem publica links quebrados. As métricas reproduzíveis estão em [`archive/coverage.json`](archive/coverage.json).

## Princípios

- somente o fórum e os recursos necessários para reproduzi-lo;
- bytes originais preservados por SHA-256;
- proveniência por URL original, captura, digest CDX e timestamp;
- texto convertido corretamente de Windows-1252/ISO-8859-1 ou UTF-8 misto;
- saída normalizada em Unicode NFC e publicada exclusivamente em UTF-8;
- páginas completas e os temas originais preservados, sem reconstruir o fórum como um catálogo moderno;
- imagens, CSS, avatares, emoticons e anexos servidos localmente;
- todos os links internos apontam para páginas locais existentes; nenhum link aponta para a Wayback Machine;
- nenhuma dependência do site atual e nenhum formulário de escrita histórico funcional;
- nenhuma API ou aplicação server-side no site publicado.

Capturas posteriores ao encerramento podem ser usadas quando preservam mensagens antigas migradas. A data da captura nunca é tratada como data da mensagem; entram no site as mensagens datadas desde `2000-01-01` até a última atividade histórica comprovada. O inventário já comprova atividade em 2013; a data final será derivada do acervo integral, não presumida a partir da captura.

## Arquitetura

```text
archive/
  captures.jsonl       manifesto verificável
  blobs/<prefix>/<sha> bytes históricos imutáveis
src/unidev_archive/
  encoding.py          conversão para Unicode/UTF-8
  parser.py            extratores Snitz, Community Server, phpBB2, phpBB3 e portal
  routing.py           rotas estáticas para URLs históricas dinâmicas
  preservation.py      preservação do documento completo e neutralização ativa
  database.py          banco intermediário usado apenas no build
  mirror.py            espelho local fiel com resolução temporal de recursos
  harvest.py           inventário e download retomável das capturas
  static/              busca estática
dist/                   saída reproduzível, não versionada
```

O GitHub Actions:

1. instala dependências com `uv`;
2. valida hashes, lint e testes;
3. extrai o acervo para um SQLite temporário de build;
4. gera todas as páginas HTML em UTF-8;
5. executa [Pagefind](https://pagefind.app/) para construir o índice estático;
6. publica o artefato no GitHub Pages.

A busca roda integralmente no navegador. O SQLite nunca é publicado nem consultado em produção.

## Desenvolvimento com `uv`

Requisitos: `uv` e Node.js apenas para gerar o índice Pagefind.

```bash
uv sync --locked --dev
uv run ruff check .
uv run pytest

mkdir -p .build/acervo
GH_REPO=skhaz/unidev gh release download archive-data-v3 \
  --pattern 'unidev-archive-*-v3.tar.gz' --dir .build
printf '%s  %s\n' \
  fd344a372599b582f2ead2c875e6f3255ad4dcf5c8e2bcbbc0a660fc3d6f29fa .build/unidev-archive-core-v3.tar.gz \
  bedb6c2e4ea874d5e48cd4f670f10ddd404f5831afbc26ff189b61cd741459ea .build/unidev-archive-resources-v3.tar.gz | sha256sum -c -
tar -xzf .build/unidev-archive-core-v3.tar.gz -C .build/acervo
tar -xzf .build/unidev-archive-resources-v3.tar.gz -C .build/acervo

uv run unidev-archive rebuild \
  --manifest .build/acervo/captures.jsonl \
  --database .build/archive.sqlite3 \
  --output dist
```

Enquanto `archive/coverage.json` indicar `publication_gate: blocked`, o último comando deve terminar com `MirrorIntegrityError`; isso é o gate de integridade funcionando, não autorização para ignorá-lo. Pagefind só deve ser executado depois de um build integral aprovado:

```bash
npx --yes pagefind@1.5.2 --site dist --force-language pt
```

## Integridade e codificação

Cada entrada do manifesto integral contém:

- URL e timestamp da fonte;
- origem (`wayback`, `commoncrawl` ou `wayback-availability`);
- status, MIME e digest fornecidos pela fonte quando disponíveis;
- digest SHA-1 calculado do payload, indicação explícita de coincidência com o CDX;
- SHA-256 e tamanho dos bytes locais;
- caminho content-addressed do blob.

O release contém 47.315 registros e 2.973.298.853 bytes recuperados. Os dois arquivos publicados têm hashes e tamanhos fixados em [`archive/source.json`](archive/source.json).

O build falha se qualquer SHA-256 divergir, se um link interno não tiver página local, se um recurso obrigatório estiver ausente ou se duas páginas incompatíveis disputarem a mesma rota. Páginas antigas declaradas como ISO-8859-1 são interpretadas como Windows-1252, preservando a pontuação usada pelos navegadores da época. Páginas phpBB3 com UTF-8 e bytes Windows-1252 isolados são decodificadas por trechos, evitando mojibake como `ProgramaÃ§Ã£o`.

## Fontes e acesso responsável

O inventário usa a [CDX API oficial](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md), e o conteúdo é recuperado com replay exato `id_`. Downloads são retomáveis, com cache, baixa concorrência, `Retry-After` e backoff conforme a orientação oficial do Internet Archive em <https://archive.org/developers/bots.html>.

A disponibilidade de uma página na Wayback Machine não garante direito irrestrito de republicação. O projeto mantém a publicação histórica, minimiza dados de perfil e aceita revisão ou retirada de conteúdo quando necessária.

## Direitos

O conteúdo arquivado, nomes, mensagens, imagens e anexos permanecem sujeitos aos direitos de seus respectivos autores. A publicação neste repositório não concede uma nova licença sobre esse material.
