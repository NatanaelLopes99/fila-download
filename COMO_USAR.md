# Baixador em lote: como usar

Script em Python que baixa uma lista de links para uma pasta, renomeia os arquivos com número e título, tenta de novo quando a conexão falha e gera uma lista do que deu errado para reprocessar depois.

---

## 1. Preparar o ambiente (só na primeira vez)

**Instale o Python**, se ainda não tiver:

1. Baixe em https://www.python.org/downloads/
2. Na instalação, marque **"Add Python to PATH"**. Sem isso, o comando `python` não funciona no terminal.

**Confira se instalou:** abra o **Prompt de Comando** (tecla Windows, digite `cmd`, Enter) e rode:

```
python --version
```

Deve aparecer algo como `Python 3.12.x`.

**Instale as bibliotecas que o script usa:**

```
python -m pip install .
```

- `requests`: faz os downloads (obrigatória).
- `tqdm`: mostra a barra de progresso (opcional, mas ajuda).

A instalação declara as dependências no `pyproject.toml` e também cria o
comando `baixador`. O uso de `python baixador.py` continua funcionando.
O projeto exige Python 3.10 ou superior.

---

## 2. Organizar a pasta

Crie uma pasta de trabalho, por exemplo `C:\baixador`, e coloque nela:

```
C:\baixador\
├── baixador.py     <- o script
├── links.txt       <- você cria
└── titulos.txt     <- você cria (opcional)
```

---

## 3. Criar o links.txt

Abra o Bloco de Notas, cole **um link por linha** e salve como `links.txt`, com codificação **UTF-8** (opção no "Salvar como").

```
https://arquivos.exemplo.com/curso/aula01.mp4
https://arquivos.exemplo.com/curso/aula02.mp4
https://arquivos.exemplo.com/curso/aula03.mp4
```

Regras:

- Linhas vazias são ignoradas.
- Linhas que começam com `#` são comentários e também são ignoradas.
- A **ordem das linhas define o número** de cada arquivo: a primeira vira 001, a segunda 002, e assim por diante.

---

## 4. Criar o titulos.txt (opcional)

Também com **um título por linha, na mesma ordem dos links**:

```
Introdução
Variáveis e tipos
Estruturas de repetição
```

Se preferir não depender da ordem, use o formato `numero;titulo`:

```
1;Introdução
2;Variáveis e tipos
3;Estruturas de repetição
```

Esse segundo formato é útil quando falta algum título ou a lista está fora de ordem.

Também aceita número separado do título por tabulação (TAB) ou espaços, como
`1    Voltando Para Casa`. O número serve para associar o título ao link e não
fica repetido no nome do arquivo. Números duplicados geram um aviso e impedem
o início do lote, para evitar misturar listas de séries diferentes.

---

## 4.1. Separar em pastas (temporadas, módulos, volumes...)

No `links.txt`, escreva o nome da pasta **entre colchetes** numa linha própria. Todos os links abaixo dela vão para essa subpasta, até aparecer outra seção:

```
[Temporada 1]
https://arquivos.exemplo.com/serie/t1e01.mp4
https://arquivos.exemplo.com/serie/t1e02.mp4

[Temporada 2]
https://arquivos.exemplo.com/serie/t2e01.mp4
https://arquivos.exemplo.com/serie/t2e02.mp4
https://arquivos.exemplo.com/serie/t2e03.mp4
```

Resultado:

```
D:\Videos\Minha Serie\
├── Temporada 1\
│   ├── Serie 001 - ....mp4
│   └── Serie 002 - ....mp4
└── Temporada 2\
    ├── Serie 001 - ....mp4
    ├── Serie 002 - ....mp4
    └── Serie 003 - ....mp4
```

Regras das seções:

- O nome entre colchetes vira o nome da pasta, então pode ser qualquer coisa: `[Temporada 1]`, `[Módulo 3 - APIs]`, `[Extras]`.
- Links que vierem **antes** da primeira seção ficam na pasta principal.
- Pode ter quantas seções quiser, e a mesma pasta pode aparecer mais de uma vez.

Os nomes das pastas são ajustados automaticamente para o Windows: os símbolos
`< > : " / \ | ? *` e caracteres de controle são removidos, assim como pontos
e espaços no final. Acentos são mantidos. Nomes reservados, como `CON`, `AUX`
e `LPT1` (inclusive com extensão), recebem `_` no início. Se um nome ficar
vazio após a limpeza, a pasta será chamada `Temporada`. A seção `[]` continua
indicando a pasta principal. Cada ajuste aparece no log.

Exemplo: `[Temporada 1: Início?]` cria a pasta `Temporada 1 Início`.

**Numeração:**

- **Padrão (contínua):** a contagem segue pelas seções: 001, 002 na Temporada 1; 003, 004, 005 na Temporada 2.
- **Com `--reiniciar-numero`:** a contagem volta para 001 em cada seção, como no exemplo acima.

**Títulos com seções:** o `titulos.txt` continua sendo **um título por link, na ordem geral**. Linhas entre colchetes nele são ignoradas, então você pode repetir as mesmas seções só para se organizar:

```
[Temporada 1]
Início
O Desafio

[Temporada 2]
Novo Rumo
Perdido
Final
```

Se usar o formato `numero;titulo`, o número é a **posição geral** do link na lista (contando todas as seções), não o número dentro da temporada.

---

## 5. Rodar o script

No Prompt de Comando, entre na pasta:

```
cd C:\baixador
```

E rode, indicando a lista de links e a pasta onde salvar:

```
python baixador.py links.txt "D:\Videos\Curso Python" --titulos titulos.txt --prefixo "Curso Python"
```

A pasta de destino é criada automaticamente se não existir. Use **aspas** em qualquer caminho ou nome que tenha espaço.

Para baixar até 5 arquivos ao mesmo tempo, use:

```
python baixador.py links.txt "D:\Videos\Naruto Shipudden" --titulos titulos.txt --prefixo "Naruto Shipudden" --paralelos 5
```

`--paralelos` aceita de 1 a 10; sem essa opção, continua baixando um arquivo
por vez. Comece com 5. Mais conexões podem ajudar quando cada download recebe
pouca velocidade, mas não garantem ganho se sua conexão ou o servidor já
estiverem no limite. Se surgirem muitos timeouts ou HTTP 429, reduza o número.

Cada arquivo mantém seu próprio `.part`, com validação do tamanho informado
pelo servidor antes de ser finalizado. Uma retomada com faixa incorreta ou
um HTTP 416 sem confirmação de tamanho não finaliza o arquivo. Essa validação
detecta transferências incompletas; não substitui um checksum da fonte.
Com Ctrl+C, os downloads em andamento param e os pendentes vão para `falhas.txt`;
uma conexão sem resposta pode aguardar até o timeout para encerrar.

No modo paralelo, o terminal mostra o início e a conclusão de cada item,
sem as barras individuais. A opção `--pausa` vale por tarefa, antes de iniciar
o próximo arquivo naquele worker. Destinos com nomes em conflito fazem o lote
voltar automaticamente para um download por vez.

**Queda de internet ou roteador travado:** a espera automática já vem ativada.
Depois de um erro de conexão, o script testa acesso à internet usando dois
destinos independentes do servidor de arquivos (Microsoft e Cloudflare,
conexão TCP na porta 443, incluindo resolução DNS). Se ambos falharem, pausa
o lote e preserva os `.part`, sem consumir as tentativas dos episódios.
As tarefas que ainda transferem dados podem terminar; novas transferências
aguardam a recuperação.

Durante a pausa, testa novamente a cada 10 segundos. Retoma após duas
verificações positivas consecutivas, com esse intervalo entre elas. A espera
não tem limite de tempo: se precisar reiniciar o roteador manualmente, pode
fazê-lo enquanto o script aguarda. Ctrl+C continua encerrando e salvando os
pendentes. A detecção começa quando um download retorna erro/timeout, não
necessariamente no instante em que o roteador trava.

Se a internet responder normalmente e só o servidor de downloads falhar,
continua usando o limite de tentativas. Erros HTTP como 403/404 também mantêm
o tratamento normal. Os testes de conectividade são uma indicação de acesso
à rede e não garantem que o servidor de arquivos esteja disponível.

Para mudar o intervalo, use `--intervalo-conexao 15`. Para desativar essa
espera e voltar à contagem normal de tentativas, use `--nao-esperar-conexao`.

Resultado:

```
Curso Python 001 - Introdução.mp4
Curso Python 002 - Variáveis e tipos.mp4
Curso Python 003 - Estruturas de repetição.mp4
```

### Todas as opções

| Opção | Para que serve | Exemplo |
|---|---|---|
| `links.txt` | Arquivo com os links (obrigatório, 1º item) | `links.txt` |
| `"pasta"` | Onde salvar (obrigatório, 2º item) | `"D:\Videos\Curso"` |
| `--titulos` | Arquivo com os títulos | `--titulos titulos.txt` |
| `--prefixo` | Texto no início de cada nome | `--prefixo "Curso Python"` |
| `--reiniciar-numero` | Numeração volta a 001 em cada seção `[...]` | `--reiniciar-numero` |
| `--tentativas` | Quantas vezes tentar em falhas temporárias (padrão 5) | `--tentativas 10` |
| `--inicio` | Começar a partir do item N | `--inicio 120` |
| `--pausa` | Segundos de espera entre um download e outro (padrão 1) | `--pausa 3` |
| `--paralelos` | Arquivos simultâneos, de 1 a 10 (padrão 1) | `--paralelos 5` |
| `--intervalo-conexao` | Segundos entre verificações durante queda de internet (padrão 10) | `--intervalo-conexao 15` |
| `--nao-esperar-conexao` | Desativa a pausa automática quando a internet cai | `--nao-esperar-conexao` |
| `--uso-conexao` | Percentual máximo da velocidade medida/informada, de 1 a 100 (padrão 80) | `--uso-conexao 60` |
| `--conexao-mbps` | Opcional: informa a velocidade em Mbps e dispensa o teste automático | `--conexao-mbps 100` |

### Limitar o uso da conexão

O padrão é **80%**. Sem `--conexao-mbps`, o script mede automaticamente a banda
disponível ao iniciar, usando o endpoint de download do
[teste de velocidade da Cloudflare](https://github.com/cloudflare/speedtest#api-reference).
Não precisa informar a velocidade da internet nem responder perguntas.

Exemplo usando a medição automática e o padrão de **80%** no total:

```
python baixador.py links.txt "C:\Users\Natanael\Videos\Boruto" --titulos titulos.txt --prefixo "Boruto" --paralelos 5
```

Para mudar para **50%**, acrescente `--uso-conexao 50`. Aceita de 1 a 100.
O teste faz até três transferências de dados descartáveis, por cerca de
5 segundos ou até ler 64 MiB no total. Durante o teste, usa banda sem o limite,
pois precisa estimar a velocidade disponível. Pode demorar um pouco mais
para fechar conexões lentas por causa dos timeouts e buffers de rede.

Se já souber a velocidade ou quiser evitar esse consumo inicial, use
`--conexao-mbps 100`, por exemplo. Isso fixa a referência em 100 Mbps e
dispensa o teste. O modo automático também funciona sem terminal interativo.
Se a internet estiver caída, aguarda a recuperação antes de repetir a medição.
Se o serviço de medição falhar com a internet disponível, avisa e encerra
antes de baixar episódios; pode tentar novamente ou usar a opção manual.

Uma medição de 100 Mbps a 80% resulta em **80 Mbps**, ou aproximadamente **10 MB/s**.
O limite é compartilhado por todos os downloads, inclusive com 5 ou 10
arquivos simultâneos. O terminal mostra o cálculo no início e o comando de
reprocessamento preserva essas opções.

O controle limita a taxa média de leitura dos arquivos e pode ter pequenas
rajadas por causa dos buffers de rede. Não controla outros programas,
outros dispositivos ou o tráfego de protocolo. Para estimar o percentual
com mais fidelidade, informe a velocidade real disponível para seu computador;
o valor contratado pode variar. A medição automática é uma estimativa do acesso
à Cloudflare naquele momento; Wi-Fi, outros dispositivos e a rota podem afetar
o resultado. O script mede uma vez por execução e mantém essa referência até
encerrar. A opção 100% ainda limita ao valor medido ou informado.

### Verificar a versão do arquivo ao retomar

Os novos parciais têm um pequeno arquivo de apoio `.part.meta.json` com o
identificador do link, tamanho e validadores HTTP fornecidos pelo servidor.
Ao retomar, o script usa `If-Range` com ETag forte ou data de modificação
adequada, quando disponíveis. Se o servidor indicar mudança, recomeça do
zero para não juntar partes de versões diferentes. O arquivo de apoio é
removido depois que o download termina.

Parciais antigos continuam sendo aceitos. Se o servidor não fornecer
validadores, a retomada segue pela faixa e pelo tamanho; não há garantia
de identidade do conteúdo nesse caso. Os arquivos de apoio não contêm o
link em texto: guardam um hash para reconhecer sua origem.

### Como fica o nome do arquivo

| Você passou | Nome gerado |
|---|---|
| `--prefixo` e `--titulos` | `Curso Python 001 - Introdução.mp4` |
| Só `--prefixo` | `Curso Python 001.mp4` |
| Só `--titulos` | `001 - Introdução.mp4` |
| Nenhum dos dois | Nome original do link (`aula01.mp4`) |

A extensão (`.mp4`, `.pdf`...) vem do próprio link. Caracteres proibidos no Windows (`: ? * " < > |`) são removidos dos títulos automaticamente.

---

## 6. Acompanhar o download

Enquanto roda, aparece algo assim:

```
[1/3] #1 Curso Python 001 - Introdução.mp4
Curso Python 001 - Introduç: 45%|████▌     | 120M/266M [00:32<00:38, 3.8MB/s]
[2/3] #2 Curso Python 002 - Variáveis e tipos.mp4
  falha (1/5): ConnectionError: ... - nova tentativa em 2s
[3/3] #3 Curso Python 003 - Estruturas de repetição.mp4
  FALHOU: HTTP 404 - arquivo não encontrado

Resumo: 2 baixado(s), 0 já existiam, 1 com falha.
```

- `[2/3]` é a posição no lote; `#2` é o número do item (o que vai no nome).
- `falha (1/5)` mostra que ele vai tentar de novo sozinho.
- `FALHOU` significa que desistiu daquele item e passou para o próximo.

---

## 7. Se algo der errado

### Parar no meio

Aperte **Ctrl+C**. O que estava baixando fica salvo como `.part`.

### Continuar depois

Rode **exatamente o mesmo comando** de novo. O script:

- pula os arquivos que já terminaram;
- retoma o `.part` de onde parou, se o servidor permitir.

### Refazer só o que falhou

Na pasta de destino aparece um `falhas.txt` com cada item e o motivo:

```
[Temporada 2]
# HTTP 404 - arquivo não encontrado
4	2	https://arquivos.exemplo.com/serie/t2e02.mp4
```

Cada linha guarda a posição geral (para achar o título), o número que vai no nome e o link. As seções também são mantidas, então o arquivo volta para a pasta certa.

O resumo final já mostra o comando pronto para reprocessar. É só usar o `falhas.txt` no lugar do `links.txt`:

```
python baixador.py "D:\Videos\Curso Python\falhas.txt" "D:\Videos\Curso Python" --titulos titulos.txt --prefixo "Curso Python"
```

Os números são mantidos, então os títulos continuam batendo. Não precisa repetir o `--reiniciar-numero`. Se você corrigir um link, pode editar direto nesse arquivo antes de rodar, mantendo os dois números e os TABs.

### Ver detalhes

O `baixador.log`, na pasta de destino, guarda tudo com data e hora, inclusive erros inesperados com o rastreamento completo.

### O que cada falha quer dizer

| Mensagem | Significado | O que fazer |
|---|---|---|
| `HTTP 401` | Sem autorização ou link vencido | Gerar o link de novo na fonte |
| `HTTP 403` | Acesso proibido | Verificar se você tem permissão |
| `HTTP 404` | Arquivo não existe | Conferir o link |
| `página HTML em vez do arquivo` | O servidor mandou uma página (login, erro) | Abrir o link no navegador para ver |
| `link inválido` | Link mal formatado | Corrigir no `.txt` |
| `desistiu após 5 tentativas` | Conexão instável ou servidor fora | Rodar de novo mais tarde ou aumentar `--tentativas` |
| `Parando: erro ao gravar em disco` | Disco cheio ou sem permissão | Liberar espaço ou trocar a pasta |

### Erros comuns ao rodar

| Erro no terminal | Causa | Solução |
|---|---|---|
| `'python' não é reconhecido...` | Python fora do PATH | Reinstalar marcando "Add Python to PATH", ou usar `py` no lugar de `python` |
| `ModuleNotFoundError: No module named 'requests'` | Biblioteca não instalada | `pip install requests tqdm` |
| `Arquivo não encontrado: links.txt` | Terminal em outra pasta | Dar `cd` para a pasta do script ou passar o caminho completo |
| Acentos estranhos nos nomes | `.txt` salvo em ANSI | Salvar os `.txt` como UTF-8 |
| `the following arguments are required: pasta` | Faltou a pasta de destino | Passar o 2º argumento entre aspas |

---

## Ajuda rápida

```
python baixador.py --help
```

Código de saída: **0** quando tudo foi baixado, **1** quando algum item falhou (útil para encadear com outros scripts).
