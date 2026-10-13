# Baixador em lote

Baixa arquivos HTTP/HTTPS a partir de uma lista de links, organiza em pastas
por temporada ou arco e monta nomes com número e título.

Funciona com **Python 3.10 ou superior**. O comando original
`python baixador.py` continua disponível.

## Recursos

- Até 10 arquivos simultâneos, com associação fixa entre link, título e pasta.
- Retomada de arquivos `.part` quando o servidor aceita.
- Limite de velocidade compartilhado entre todos os arquivos: padrão de 80%.
- Medição automática da conexão ao iniciar, ou velocidade manual em Mbps.
- Espera automática quando a internet cai, sem gastar tentativas dos itens.
- Nomes compatíveis com Windows, mantendo acentos.
- Registro em `baixador.log` e reprocessamento apenas dos itens de `falhas.txt`.

## Instalação

Na pasta do projeto:

```console
python -m venv .venv
```

Ative o ambiente no PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

No Linux/macOS:

```sh
source .venv/bin/activate
```

Instale o projeto:

```console
python -m pip install .
```

Também aceita `python -m pip install -r requirements.txt`. Se preferir não
ativar o ambiente no Windows, use `.venv\Scripts\python.exe` no lugar de `python`.

## Preparar as listas

Copie `links.exemplo.txt` e `titulos.exemplo.txt` para `links.txt` e
`titulos.txt`, respectivamente, e substitua os exemplos pelos seus dados.
Os exemplos usam endereços fictícios.

`links.txt`, em UTF-8:

```text
[01 - Temporada 1]
https://arquivos.example.com/episodio-001.mp4
https://arquivos.example.com/episodio-002.mp4

[02 - Temporada 2]
https://arquivos.example.com/episodio-003.mp4
```

`titulos.txt`, também em UTF-8:

```text
1;Primeiro episódio
2;Segundo episódio
3;Uma nova temporada
```

Aceita `número;titulo`, número separado por TAB/espaços ou títulos sem número
na ordem dos links. Linhas vazias, comentários `#` e cabeçalhos `[seção]`
não contam como episódios. O número do título corresponde à posição geral
na lista, mesmo com `--reiniciar-numero`.

## Baixar

```console
python baixador.py links.txt "downloads/Minha Serie" --titulos titulos.txt --prefixo "Minha Serie" --paralelos 5
```

Depois de instalar, também pode usar `baixador` no lugar de `python baixador.py`.
O terminal mostra o caminho completo onde os downloads serão salvos.

Exemplo de resultado:

```text
downloads/Minha Serie/
├── 01 - Temporada 1/
│   ├── Minha Serie 001 - Primeiro episódio.mp4
│   └── Minha Serie 002 - Segundo episódio.mp4
└── 02 - Temporada 2/
    └── Minha Serie 003 - Uma nova temporada.mp4
```

Sem `--paralelos`, baixa um arquivo por vez. Para usar 50% da banda estimada,
adicione `--uso-conexao 50`. Para evitar o teste inicial, informe uma referência
manual, por exemplo `--conexao-mbps 100 --uso-conexao 80`.

## Retomar ou reprocessar

Ctrl+C mantém os parciais e registra os pendentes. Rode o mesmo comando para
continuar, ou use somente o arquivo de falhas:

```console
python baixador.py "downloads/Minha Serie/falhas.txt" "downloads/Minha Serie" --titulos titulos.txt --prefixo "Minha Serie" --paralelos 2
```

O `falhas.txt` preserva a temporada, o número e o link. Após cada execução,
ele fica apenas com as falhas atuais; se todos os itens terminarem, é removido.
Arquivos concluídos com o nome esperado são pulados. Alterar título ou prefixo
pode mudar o destino e gerar um novo download.

## Limites e comportamento

- A medição inicial usa o [endpoint de teste da Cloudflare](https://github.com/cloudflare/speedtest#api-reference)
  por cerca de 5 segundos, lendo até 64 MiB. Ela usa banda sem limitação para
  estimar a velocidade disponível. O percentual é aplicado depois.
- O limite controla a taxa média de leitura dos arquivos, com possíveis
  pequenas rajadas de buffers. Não controla outros dispositivos ou programas.
- O script verifica o tamanho quando o servidor o informa. Essa verificação
  não substitui um checksum publicado pela fonte.
- A retomada usa validadores HTTP quando fornecidos pelo servidor. Sem esses
  validadores, não há garantia de que o conteúdo permaneceu igual.
- A detecção de queda começa após um erro/timeout. Ctrl+C pode aguardar uma
  requisição em andamento atingir seu timeout antes de encerrar.
- A medição é feita uma vez por execução; disponibilidade do serviço,
  congestionamento e Wi-Fi podem afetar a estimativa.

O [guia completo](COMO_USAR.md) explica todas as opções. Para listar os comandos:

```console
python baixador.py --help
```

## Desenvolvimento

```console
python -B -m unittest -v test_baixador
```

Os testes usam servidor HTTP local e simulações, sem baixar os arquivos das
listas pessoais ou executar um teste real da conexão. O GitHub Actions
executa a suíte no Windows e Linux com Python 3.10 e 3.14.

O pacote publica apenas o módulo do baixador. Listas pessoais, arquivos baixados,
parciais e logs são ignorados pelo Git; exemplos fictícios ficam no repositório.

## Licença

[MIT](LICENSE).
