# Arquivo histórico do fórum UniDev

Restauração estática, pesquisável e somente leitura do fórum brasileiro **UniDev**, limitada às mensagens publicadas entre **2000 e 2009**.

- Site: <https://skhaz.github.io/unidev/>
- Repositório: <https://github.com/skhaz/unidev>
- Fonte primária: <https://web.archive.org/>

> **Estado atual:** a infraestrutura de preservação e publicação está funcional, mas a importação integral ainda está em andamento. O primeiro conjunto publicado é uma amostra verificável do acervo, não uma alegação de completude.

## Princípios

- somente o fórum e os recursos necessários para reproduzi-lo;
- bytes originais preservados por SHA-256;
- proveniência por URL original, captura, digest CDX e timestamp;
- texto convertido corretamente de Windows-1252/ISO-8859-1 ou UTF-8 misto;
- saída normalizada em Unicode NFC e publicada exclusivamente em UTF-8;
- imagens, CSS, JavaScript, avatares, emoticons e anexos recuperados quando disponíveis;
- nenhuma dependência do site atual e nenhum formulário histórico funcional;
- nenhuma API ou aplicação server-side no site publicado.

Capturas posteriores a 2009 podem ser usadas quando preservam mensagens antigas migradas. A data da captura nunca é tratada como data da mensagem; somente conteúdo datado entre `2000-01-01` e `2009-12-31` entra no site.

## Arquitetura

```text
archive/
  captures.jsonl       manifesto verificável
  blobs/<prefix>/<sha> bytes históricos imutáveis
src/unidev_archive/
  encoding.py          conversão para Unicode/UTF-8
  parser.py            extratores Snitz, phpBB2 e phpBB3
  database.py          banco intermediário usado apenas no build
  site.py              gerador de HTML estático
  static/              interface de busca e estilos
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
uv run unidev-archive rebuild \
  --manifest archive/captures.jsonl \
  --database .build/archive.sqlite3 \
  --output dist
npx --yes pagefind@1.5.2 --site dist --force-language pt
```

## Integridade e codificação

Cada entrada de `archive/captures.jsonl` contém:

- URL e timestamp históricos;
- status e MIME informados pelo CDX;
- digest do Internet Archive;
- SHA-256 e tamanho dos bytes locais;
- caminho content-addressed do blob.

O build falha se qualquer SHA-256 divergir. Páginas antigas declaradas como ISO-8859-1 são interpretadas como Windows-1252, preservando a pontuação usada pelos navegadores da época. Páginas phpBB3 com UTF-8 e bytes Windows-1252 isolados são decodificadas por trechos, evitando mojibake como `ProgramaÃ§Ã£o`.

## Fontes e acesso responsável

O inventário usa a [CDX API oficial](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md), e o conteúdo é recuperado com replay exato `id_`. Downloads são retomáveis, com cache, baixa concorrência, `Retry-After` e backoff conforme a orientação oficial do Internet Archive em <https://archive.org/developers/bots.html>.

A disponibilidade de uma página na Wayback Machine não garante direito irrestrito de republicação. O projeto mantém a publicação histórica, minimiza dados de perfil e aceita revisão ou retirada de conteúdo quando necessária.

## Direitos

O conteúdo arquivado, nomes, mensagens, imagens e anexos permanecem sujeitos aos direitos de seus respectivos autores. A publicação neste repositório não concede uma nova licença sobre esse material.
