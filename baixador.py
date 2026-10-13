#!/usr/bin/env python3
"""
Baixador em lote genérico, com tratamento de erros e pastas por seção.

Uso:
    python baixador.py links.txt "D:/Destino" [--titulos titulos.txt] [--prefixo "Serie"]
                       [--reiniciar-numero] [--tentativas 5] [--inicio N] [--pausa 1]
                       [--paralelos 5] [--conexao-mbps 100] [--uso-conexao 80]

links.txt   -> um link por linha (linhas vazias e iniciadas com # são ignoradas).
               Uma linha entre colchetes abre uma seção/pasta, ex.: [Temporada 1]
               Os links abaixo dela vão para a subpasta "Temporada 1", até a próxima seção.
               Também aceita "N<TAB>link" ou "N<TAB>M<TAB>link" (formato do falhas.txt).
titulos.txt -> opcional, um título por linha na ordem geral dos links, ou "numero;titulo",
               "numero<TAB>titulo" ou "numero titulo".
               Linhas entre colchetes são ignoradas, então dá para repetir as seções
               ali só para se organizar.

--reiniciar-numero -> a numeração no nome volta para 001 em cada seção
                      (sem isso, a numeração é contínua: 001, 002... 027, 028...).

Tratamento de erros:
  - Sem internet -> pausa compartilhada até a conexão voltar, sem gastar tentativas.
  - Erros temporários (queda de conexão, timeout, 5xx, 408, 429) -> nova tentativa
    com espera crescente; 429 respeita o cabeçalho Retry-After.
  - Erros definitivos (401, 403, 404, 410, link inválido) -> não insiste, registra e segue.
  - Página HTML no lugar do arquivo -> tratado como falha.
  - Arquivo incompleto -> retoma/tenta de novo.
  - Disco cheio ou sem permissão -> interrompe tudo.
  - Ctrl+C -> para com segurança, mantém o .part e salva os pendentes.
  - Qualquer erro inesperado em um item não derruba os demais.

Saídas na pasta de destino:
  baixador.log -> registro detalhado com data/hora
  falhas.txt   -> itens que falharam, com as seções preservadas (use como links.txt para refazer)
"""

import argparse
import errno
import hashlib
import json
import logging
import math
import re
import socket
import sys
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event, Lock
from urllib.parse import unquote, urlparse

import requests

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

CHUNK = 1024 * 1024
INVALIDOS = r'[<>:"/\\|?*\x00-\x1f]'
RESERVADOS = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", re.IGNORECASE)
STATUS_TEMPORARIOS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
SECAO = re.compile(r"^\[(.*)\]$")
log = logging.getLogger("baixador")


@dataclass
class Item:
    indice: int   # posição geral na lista (liga o link ao título)
    numero: int   # número que vai no nome do arquivo
    url: str
    secao: str    # subpasta ("" = pasta principal)


class ErroDefinitivo(Exception):
    """Não adianta tentar de novo (ex.: 401, 404, link inválido)."""


class ErroTemporario(Exception):
    """Pode funcionar numa nova tentativa."""

    def __init__(self, msg, espera=None):
        super().__init__(msg)
        self.espera = espera


class ErroFatal(Exception):
    """Problema local que impede continuar (disco cheio, sem permissão)."""


class ErroInterrompido(Exception):
    """Mantém o parcial quando o lote é interrompido."""


def calcular_limite(conexao_mbps, percentual):
    if not math.isfinite(conexao_mbps) or conexao_mbps <= 0:
        raise ValueError("A velocidade da conexão deve ser um número positivo em Mbps.")
    if not 1 <= percentual <= 100:
        raise ValueError("O uso da conexão deve estar entre 1 e 100%.")
    taxa = conexao_mbps * 1_000_000 / 8 * percentual / 100
    if not math.isfinite(taxa) or taxa <= 0:
        raise ValueError("Velocidade da conexão fora do intervalo suportado.")
    return taxa


def medir_velocidade(duracao=5, max_bytes=64 * 1024 * 1024, trabalhadores=3,
                     url="https://speed.cloudflare.com/__down", parar=None):
    """Estima a banda disponível por transferência real, com tempo e volume limitados."""
    parar = parar or Event()
    trava = Lock()
    inicio = time.monotonic()
    prazo = inicio + duracao
    reservados = recebidos = 0

    def medir():
        nonlocal reservados, recebidos
        if parar.is_set():
            return None
        try:
            with requests.Session() as sessao:
                with sessao.get(url, params={"bytes": max_bytes}, stream=True,
                                headers={"Accept-Encoding": "identity"}, timeout=(3, 2)) as resposta:
                    resposta.raise_for_status()
                    if "text/html" in resposta.headers.get("Content-Type", "").lower():
                        raise requests.RequestException("teste devolveu HTML em vez de dados")
                    blocos = resposta.iter_content(16 * 1024)
                    while time.monotonic() < prazo and not parar.is_set():
                        with trava:
                            quantidade = min(16 * 1024, max_bytes - reservados)
                            if quantidade <= 0:
                                break
                            reservados += quantidade
                        try:
                            bloco = next(blocos)
                        except StopIteration:
                            break
                        with trava:
                            recebidos += len(bloco[:quantidade])
            return None
        except requests.RequestException as e:
            return str(e)

    executor = ThreadPoolExecutor(max_workers=trabalhadores)
    try:
        futuros = [executor.submit(medir) for _ in range(trabalhadores)]
        erros = [erro for futuro in as_completed(futuros) if (erro := futuro.result())]
    except KeyboardInterrupt:
        parar.set()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=parar.is_set())
    if parar.is_set():
        raise ErroInterrompido()
    tempo = time.monotonic() - inicio
    if recebidos < 16 * 1024 or tempo <= 0 or len(erros) == trabalhadores:
        motivo = erros[0] if erros else "não recebeu dados suficientes"
        raise ErroTemporario(f"não foi possível medir a conexão: {motivo}")
    return recebidos * 8 / tempo / 1_000_000


class LimitadorVelocidade:
    """Reserva tempo de leitura em uma fila compartilhada por todos os arquivos."""

    def __init__(self, bytes_por_segundo, parar):
        self.taxa = bytes_por_segundo
        self.parar = parar
        self.trava = Lock()
        self.proxima_leitura = 0.0
        # Blocos pequenos reduzem rajadas e mantêm Ctrl+C responsivo.
        self.bloco = max(1024, min(64 * 1024, int(self.taxa * 0.05)))

    def reservar(self, tamanho):
        with self.trava:
            agora = time.monotonic()
            self.proxima_leitura = max(agora, self.proxima_leitura) + tamanho / self.taxa
            return self.proxima_leitura - agora

    def aguardar(self, tamanho):
        if self.parar.is_set() or self.parar.wait(self.reservar(tamanho)):
            raise ErroInterrompido()


class MonitorConexao:
    """Uma única verificação/espera de internet para todos os downloads."""

    def __init__(self, parar, intervalo=10, testar=None):
        self.parar = parar
        self.intervalo = intervalo
        self.testar = testar or self.testar_internet
        self.trava = Lock()
        self.online = Event()
        self.online.set()
        self.geracao = 0

    def testar_internet(self):
        # Testa destinos independentes do servidor de arquivos, incluindo DNS.
        for host in ("www.microsoft.com", "www.cloudflare.com"):
            if self.parar.is_set():
                raise ErroInterrompido()
            try:
                with socket.create_connection((host, 443), timeout=3):
                    return True
            except OSError:
                continue
        return False

    def aguardar(self):
        while not self.online.wait(0.2):
            if self.parar.is_set():
                raise ErroInterrompido()
        if self.parar.is_set():
            raise ErroInterrompido()
        return self.geracao

    def apos_erro(self, geracao):
        while not self.trava.acquire(timeout=0.2):
            if self.parar.is_set():
                raise ErroInterrompido()
        try:
            if self.parar.is_set():
                raise ErroInterrompido()
            # Outro worker já esperou essa queda: não desconta outra tentativa.
            if geracao != self.geracao:
                return True
            if self.testar():
                return False
            self.online.clear()
            log.warning("Internet indisponível. Pausando o lote sem gastar tentativas; "
                        "aguardando a conexão voltar (Ctrl+C para parar).")
            sucessos = 0
            while sucessos < 2:
                if self.parar.wait(self.intervalo):
                    raise ErroInterrompido()
                sucessos = sucessos + 1 if self.testar() else 0
            self.geracao += 1
            self.online.set()
            log.info("Conexão voltou e passou em duas verificações. Retomando os downloads.")
            return True
        finally:
            self.trava.release()


# ---------------------------------------------------------------- entrada

def limpar_nome(nome: str) -> str:
    nome = re.sub(INVALIDOS, "", nome).strip()
    nome = re.sub(r"\s+", " ", nome)[:180].rstrip(" .")
    # Nomes de dispositivos são proibidos mesmo com extensão (ex.: CON.txt).
    if RESERVADOS.fullmatch(nome.split(".", 1)[0].rstrip(" ")):
        nome = "_" + nome
    return nome


def ler_links(caminho: Path, reiniciar: bool) -> list[Item]:
    itens: list[Item] = []
    secao, seq, cont_secao = "", 0, 0
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        m = SECAO.match(linha)
        if m:
            original = m.group(1)
            secao = (limpar_nome(original) or "Temporada") if original.strip() else ""
            cont_secao = 0
            if secao != original:
                log.info("Nome da pasta ajustado para o Windows: %r -> %r", original, secao)
            continue
        m = re.match(r"^(\d+)\t(?:(\d+)\t)?(.+)$", linha)
        if m:
            indice = int(m.group(1))
            numero = int(m.group(2)) if m.group(2) else None
            url = m.group(3).strip()
        else:
            seq += 1
            indice, numero, url = seq, None, linha
        cont_secao += 1
        if numero is None:
            numero = cont_secao if reiniciar else indice
        itens.append(Item(indice, numero, url, secao))
    return itens


def ler_titulos(caminho: Path | None) -> dict[int, str]:
    if not caminho:
        return {}
    titulos, i = {}, 0
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or SECAO.match(linha):
            continue
        m = re.match(r"^(\d+)(?:\s*;\s*|\s+)(\S.*)$", linha)
        if m:
            numero = int(m.group(1))
            if numero in titulos:
                raise ValueError(f"Título repetido para o episódio {numero}; confira {caminho}.")
            titulos[numero] = m.group(2)
            i = max(i, numero)
        else:
            i += 1
            titulos[i] = linha
    return titulos


def nome_destino(item: Item, digitos: int, titulos: dict, prefixo: str) -> str:
    original = unquote(Path(urlparse(item.url).path).name) or f"arquivo_{item.indice}"
    ext = Path(original).suffix or ".bin"
    num = str(item.numero).zfill(digitos)
    titulo = titulos.get(item.indice)
    if prefixo:
        base = f"{prefixo} {num}" + (f" - {titulo}" if titulo else "")
    elif titulo:
        base = f"{num} - {titulo}"
    else:
        return limpar_nome(original) or f"arquivo_{item.indice}{ext}"
    return (limpar_nome(base) or f"arquivo_{item.indice}") + ext


def validar_url(url: str):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ErroDefinitivo(f"link inválido: {url!r}")


# ---------------------------------------------------------------- download

def caminho_metadados(parcial: Path) -> Path:
    return parcial.with_name(parcial.name + ".meta.json")


def ler_metadados(parcial: Path) -> dict:
    try:
        dados = json.loads(caminho_metadados(parcial).read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def salvar_metadados(parcial: Path, url, resposta, tamanho):
    caminho = caminho_metadados(parcial)
    temporario = caminho.with_name(caminho.name + ".tmp")
    anteriores = ler_metadados(parcial) if resposta.status_code == 206 else {}
    dados = {
        "origem": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "etag": resposta.headers.get("ETag", anteriores.get("etag")),
        "last_modified": resposta.headers.get("Last-Modified", anteriores.get("last_modified")),
        "date": resposta.headers.get("Date", anteriores.get("date")),
        "tamanho": tamanho,
    }
    try:
        temporario.write_text(json.dumps(dados), encoding="utf-8")
        temporario.replace(caminho)
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EACCES, errno.EROFS):
            raise ErroFatal(f"erro ao salvar dados de retomada: {e.strerror}") from e
        raise


def validador_metadados(dados):
    etag = dados.get("etag")
    if isinstance(etag, str) and etag.startswith('"') and etag.endswith('"'):
        return "ETag", etag
    modificado = dados.get("last_modified")
    if not etag and isinstance(modificado, str) and modificado:
        try:
            # RFC 9110: a data precisa ser um validador forte para usar If-Range.
            data = parsedate_to_datetime(dados.get("date", ""))
            alteracao = parsedate_to_datetime(modificado)
            if (data - alteracao).total_seconds() >= 60:
                return "Last-Modified", modificado
        except (TypeError, ValueError, OverflowError):
            pass
    return None, None


def classificar_status(r: requests.Response):
    if r.status_code in (200, 206):
        return
    if r.status_code in STATUS_TEMPORARIOS:
        espera = None
        if r.status_code == 429 and r.headers.get("Retry-After", "").isdigit():
            espera = int(r.headers["Retry-After"])
        raise ErroTemporario(f"HTTP {r.status_code}", espera)
    motivos = {401: "não autorizado (link vencido ou sem acesso)", 403: "acesso proibido",
               404: "arquivo não encontrado", 410: "arquivo removido"}
    raise ErroDefinitivo(f"HTTP {r.status_code} - {motivos.get(r.status_code, r.reason)}")


def baixar_uma_vez(sessao, url, destino: Path, parcial: Path, parar=None, progresso=True,
                   limitador=None, retomar=True):
    ja = parcial.stat().st_size if parcial.exists() and retomar else 0
    dados = ler_metadados(parcial) if ja else {}
    origem = hashlib.sha256(url.encode("utf-8")).hexdigest()
    if dados.get("origem") and dados["origem"] != origem:
        log.info("  link mudou; recomeçando o parcial do zero")
        ja, dados = 0, {}
    campo, validador = validador_metadados(dados)
    headers = {"Accept-Encoding": "identity"}
    if ja:
        headers["Range"] = f"bytes={ja}-"
        if validador:
            headers["If-Range"] = validador

    with sessao.get(url, headers=headers, stream=True, timeout=(15, 60)) as r:
        if r.status_code == 416 and ja:
            if validador and r.headers.get(campo) != validador:
                log.info("  servidor não confirmou a versão do parcial; recomeçando do zero")
                r.close()
                return baixar_uma_vez(sessao, url, destino, parcial, parar, progresso,
                                      limitador, retomar=False)
            # Só aceita o parcial se o servidor confirmar o tamanho total.
            if r.headers.get("Content-Range") == f"bytes */{ja}":
                return
            raise ErroDefinitivo("servidor não confirmou o tamanho do parcial; arquivo não finalizado")
        classificar_status(r)

        if r.headers.get("Content-Type", "").startswith("text/html"):
            raise ErroDefinitivo("o servidor devolveu uma página HTML em vez do arquivo")

        if ja and r.status_code != 206:          # servidor ignorou a retomada
            log.info("  servidor não aceita retomada; recomeçando do zero")
            ja = 0
        esperado = int(r.headers["Content-Length"]) + ja if r.headers.get("Content-Length") else None
        if r.status_code == 206:
            faixa = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", r.headers.get("Content-Range", ""))
            if not faixa or int(faixa[1]) != ja or int(faixa[2]) != int(faixa[3]) - 1:
                raise ErroDefinitivo("resposta de retomada inválida; arquivo não finalizado")
            esperado = int(faixa[3])
            mudou = (validador and r.headers.get(campo, validador) != validador)
            mudou = mudou or (dados.get("tamanho") is not None and dados["tamanho"] != esperado)
            if mudou:
                log.info("  arquivo no servidor mudou; recomeçando do zero")
                r.close()
                return baixar_uma_vez(sessao, url, destino, parcial, parar, progresso,
                                      limitador, retomar=False)
        if r.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ErroDefinitivo("servidor enviou conteúdo comprimido; tamanho não pode ser validado")
        salvar_metadados(parcial, url, r, esperado)

        barra = (tqdm(total=esperado, initial=ja, unit="B", unit_scale=True,
                      desc=destino.name[:40], leave=False) if tqdm and progresso else None)
        try:
            with open(parcial, "ab" if ja else "wb") as f:
                tamanho_bloco = limitador.bloco if limitador is not None else CHUNK
                blocos = r.iter_content(tamanho_bloco)
                recebidos = ja
                while True:
                    if parar is not None and parar.is_set():
                        raise ErroInterrompido()
                    if limitador is not None:
                        reserva = tamanho_bloco if esperado is None else min(tamanho_bloco, max(0, esperado - recebidos))
                        if reserva:
                            limitador.aguardar(reserva)
                    try:
                        bloco = next(blocos)
                    except StopIteration:
                        break
                    f.write(bloco)
                    recebidos += len(bloco)
                    if barra:
                        barra.update(len(bloco))
        except OSError as e:
            if e.errno in (errno.ENOSPC, errno.EACCES, errno.EROFS):
                raise ErroFatal(f"erro ao gravar em disco: {e.strerror}") from e
            raise
        finally:
            if barra:
                barra.close()

    tamanho = parcial.stat().st_size
    if esperado is not None and tamanho != esperado:
        raise ErroTemporario(f"download incompleto ({tamanho} de {esperado} bytes)")
    if tamanho == 0:
        raise ErroTemporario("arquivo veio vazio")


def baixar(sessao, url, destino: Path, tentativas: int, parar=None, progresso=True,
           conexao=None, limitador=None):
    validar_url(url)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ErroFatal(f"não foi possível criar a pasta {destino.parent}: {e.strerror}") from e
    parcial = destino.with_name(destino.name + ".part")
    ultimo = "erro desconhecido"

    tentativa = 1
    while tentativa <= tentativas:
        if parar is not None and parar.is_set():
            raise ErroInterrompido()
        geracao = conexao.aguardar() if conexao is not None else None
        try:
            baixar_uma_vez(sessao, url, destino, parcial, parar, progresso, limitador)
            if parar is not None and parar.is_set():
                raise ErroInterrompido()
            parcial.replace(destino)
            try:
                caminho_metadados(parcial).unlink(missing_ok=True)
            except OSError:
                log.warning("  arquivo concluído, mas não foi possível remover os dados de retomada")
            return
        except (ErroDefinitivo, ErroFatal):
            raise
        except ErroTemporario as e:
            ultimo, espera = str(e), e.espera
        except requests.exceptions.SSLError as e:
            raise ErroDefinitivo(f"erro de certificado SSL: {e}") from e
        except requests.exceptions.InvalidURL as e:
            raise ErroDefinitivo(f"link inválido: {e}") from e
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            ultimo, espera = f"{type(e).__name__}: {e}", None
            if conexao is not None and conexao.apos_erro(geracao):
                # O .part foi fechado antes da espera; retoma do mesmo item.
                continue
        except OSError as e:
            ultimo, espera = f"erro de arquivo: {e}", None

        if tentativa < tentativas:
            espera = espera or min(60, 2 ** tentativa)
            log.warning(f"  falha ({tentativa}/{tentativas}): {ultimo} - nova tentativa em {espera}s")
            if parar is None:
                time.sleep(espera)
            elif parar.wait(espera):
                raise ErroInterrompido()
        tentativa += 1

    raise ErroTemporario(f"desistiu após {tentativas} tentativas: {ultimo}")


# ---------------------------------------------------------------- principal

def configurar_log(pasta: Path):
    log.setLevel(logging.INFO)
    tela = logging.StreamHandler(sys.stdout)
    tela.setFormatter(logging.Formatter("%(message)s"))
    arquivo = logging.FileHandler(pasta / "baixador.log", encoding="utf-8")
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(tela)
    log.addHandler(arquivo)


def salvar_falhas(pasta: Path, falhas: list[tuple[Item, str]]):
    arq = pasta / "falhas.txt"
    if not falhas:
        if arq.exists():
            arq.unlink()
        return
    linhas, secao_atual = [], None
    for item, motivo in falhas:
        if item.secao != secao_atual:
            secao_atual = item.secao
            linhas.append(f"\n[{item.secao}]" if item.secao else "\n[]")
        linhas.append(f"# {motivo}\n{item.indice}\t{item.numero}\t{item.url}")
    arq.write_text("\n".join(linhas).lstrip() + "\n", encoding="utf-8")


def executar_lote(itens, pasta, digitos, titulos, prefixo, tentativas, pausa, paralelos,
                  esperar_conexao=True, intervalo_conexao=10, limite_bytes=None):
    destinos = [pasta / it.secao / nome_destino(it, digitos, titulos, prefixo) for it in itens]
    # Impede dois workers de escreverem no mesmo arquivo ou no mesmo .part.
    caminhos = [str(p.resolve()).casefold() for p in destinos]
    arquivos_em_uso = set()
    for caminho in caminhos:
        ocupados = {caminho, caminho + ".part", caminho + ".part.meta.json",
                    caminho + ".part.meta.json.tmp"}
        if ocupados & arquivos_em_uso:
            if paralelos > 1:
                log.warning("Nomes de destino em conflito; usando 1 download por vez.")
                paralelos = 1
            break
        arquivos_em_uso.update(ocupados)

    parar = Event()
    conexao = MonitorConexao(parar, intervalo_conexao) if esperar_conexao else None
    limitador = LimitadorVelocidade(limite_bytes, parar) if limite_bytes is not None else None

    def processar(pos):
        item, destino = itens[pos], destinos[pos]
        if parar.is_set():
            raise ErroInterrompido()
        rotulo = f"[{pos + 1}/{len(itens)}] #{item.indice} [{item.secao or 'pasta principal'}]"
        try:
            if destino.exists() and destino.stat().st_size > 0:
                log.info("%s já existe: %s", rotulo, destino.name)
                return "pulado"
            log.info("%s baixando: %s", rotulo, destino.name)
            # Cada download tem sua própria sessão e seu próprio arquivo parcial.
            with requests.Session() as sessao:
                sessao.headers["User-Agent"] = "baixador-lote/1.3"
                baixar(sessao, item.url, destino, tentativas, parar, paralelos == 1, conexao, limitador)
            log.info("%s concluído: %s", rotulo, destino.name)
            return "ok"
        except ErroFatal:
            parar.set()
            raise
        except (ErroDefinitivo, ErroTemporario) as e:
            log.error("#%s FALHOU: %s", item.indice, e)
            raise
        except ErroInterrompido:
            raise
        except Exception:
            log.exception("#%s erro inesperado", item.indice)
            raise
        finally:
            if pausa > 0:
                parar.wait(pausa)

    log.info("Downloads simultâneos: %s", paralelos)
    if conexao is not None:
        log.info("Espera automática por conexão ativada (verificação a cada %ss).", intervalo_conexao)
    executor = ThreadPoolExecutor(max_workers=paralelos)
    futuros = {}
    try:
        for pos in range(len(itens)):
            futuros[executor.submit(processar, pos)] = pos
        for futuro in as_completed(futuros):
            if not futuro.cancelled() and isinstance(futuro.exception(), ErroFatal):
                parar.set()
                break
    except KeyboardInterrupt:
        parar.set()
        log.warning("\nInterrompido pelo usuário. Mantendo os .part e salvando pendentes...")
    finally:
        executor.shutdown(wait=True, cancel_futures=parar.is_set())

    ok = pulados = 0
    falhas = []
    for futuro, pos in sorted(futuros.items(), key=lambda par: par[1]):
        item = itens[pos]
        try:
            resultado = futuro.result()
            ok += resultado == "ok"
            pulados += resultado == "pulado"
        except (CancelledError, ErroInterrompido):
            falhas.append((item, "não processado (execução interrompida)"))
        except ErroFatal as e:
            log.critical("Parando: %s", e)
            falhas.append((item, str(e)))
        except (ErroDefinitivo, ErroTemporario) as e:
            falhas.append((item, str(e)))
        except Exception as e:
            falhas.append((item, f"erro inesperado: {e}"))
    # Se Ctrl+C interromper a submissão, inclui os itens ainda não agendados.
    agendados = set(futuros.values())
    falhas += [(it, "não processado (execução interrompida)")
               for pos, it in enumerate(itens) if pos not in agendados]
    return ok, pulados, falhas


def main():
    ap = argparse.ArgumentParser(description="Baixa uma lista de links para uma pasta.")
    ap.add_argument("links", type=Path)
    ap.add_argument("pasta", type=Path)
    ap.add_argument("--titulos", type=Path)
    ap.add_argument("--prefixo", default="")
    ap.add_argument("--reiniciar-numero", action="store_true",
                    help="numeração volta a 001 em cada seção [..]")
    ap.add_argument("--tentativas", type=int, default=5)
    ap.add_argument("--inicio", type=int, default=1, help="pular itens com número geral menor que N")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre downloads")
    ap.add_argument("--paralelos", type=int, choices=range(1, 11), default=1,
                    help="arquivos simultâneos (1 a 10; padrão 1; comece com 5)")
    ap.add_argument("--intervalo-conexao", type=float, default=10,
                    help="segundos entre verificações durante queda de internet (padrão 10)")
    ap.add_argument("--nao-esperar-conexao", action="store_true",
                    help="desativa a pausa automática quando a internet cai")
    ap.add_argument("--uso-conexao", type=int, default=80, metavar="1-100",
                    help="percentual máximo da velocidade medida/informada (1 a 100; padrão 80)")
    ap.add_argument("--conexao-mbps", type=float,
                    help="opcional: define a velocidade em Mbps e dispensa o teste automático")
    args = ap.parse_args()
    if not math.isfinite(args.pausa) or args.pausa < 0 or args.tentativas < 1:
        ap.error("--pausa deve ser >= 0 e --tentativas deve ser >= 1")
    if not math.isfinite(args.intervalo_conexao) or args.intervalo_conexao <= 0:
        ap.error("--intervalo-conexao deve ser > 0")
    if not 1 <= args.uso_conexao <= 100:
        ap.error("--uso-conexao deve estar entre 1 e 100")

    for arq in (args.links, args.titulos):
        if arq and not arq.is_file():
            sys.exit(f"Arquivo não encontrado: {arq}")
    velocidade_manual = args.conexao_mbps is not None
    if velocidade_manual:
        try:
            calcular_limite(args.conexao_mbps, args.uso_conexao)
        except ValueError as e:
            ap.error(str(e))
    try:
        args.pasta.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.exit(f"Não foi possível criar a pasta {args.pasta}: {e.strerror}")

    configurar_log(args.pasta)
    log.info("Pasta dos downloads: %s", args.pasta.resolve())
    itens = [it for it in ler_links(args.links, args.reiniciar_numero) if it.indice >= args.inicio]
    if not itens:
        sys.exit("Nenhum link para baixar.")
    try:
        titulos = ler_titulos(args.titulos)
    except ValueError as e:
        sys.exit(str(e))
    digitos = max(3, len(str(max(it.numero for it in itens))))

    if not velocidade_manual:
        log.info("Medindo a conexão automaticamente na Cloudflare (cerca de 5s, até 64 MiB). "
                 "O teste usa banda sem o limite para estimar a velocidade disponível.")
        parar_teste = Event()
        monitor = MonitorConexao(parar_teste, args.intervalo_conexao)
        try:
            while True:
                try:
                    args.conexao_mbps = medir_velocidade(parar=parar_teste)
                    break
                except ErroTemporario:
                    if args.nao_esperar_conexao or not monitor.apos_erro(monitor.geracao):
                        raise
            log.info("Velocidade estimada: %.2f Mbps.", args.conexao_mbps)
        except KeyboardInterrupt:
            sys.exit("\nTeste cancelado. Nenhum download de episódio foi iniciado.")
        except ErroTemporario as e:
            sys.exit(f"{e}\nTente novamente ou informe --conexao-mbps para usar uma velocidade manual.")
    limite_bytes = calcular_limite(args.conexao_mbps, args.uso_conexao)
    log.info("Limite total: %s%% de %g Mbps = %g Mbps (%.2f MB/s), compartilhado entre os arquivos.",
             args.uso_conexao, args.conexao_mbps, limite_bytes * 8 / 1_000_000,
             limite_bytes / 1_000_000)

    secoes = sorted({it.secao for it in itens if it.secao})
    if secoes:
        log.info(f"{len(secoes)} seção(ões): {', '.join(secoes)}")
    sem_titulo = [it.indice for it in itens if args.titulos and it.indice not in titulos]
    if sem_titulo:
        log.warning(f"Aviso: {len(sem_titulo)} item(ns) sem título (ex.: {sem_titulo[:5]})")

    ok, pulados, falhas = executar_lote(
        itens, args.pasta, digitos, titulos, args.prefixo,
        args.tentativas, args.pausa, args.paralelos,
        not args.nao_esperar_conexao, args.intervalo_conexao,
        limite_bytes,
    )
    salvar_falhas(args.pasta, falhas)

    log.info(f"\nResumo: {ok} baixado(s), {pulados} já existiam, {len(falhas)} com falha.")
    if falhas:
        log.info(f"Para tentar de novo só as falhas:\n  python \"{Path(__file__).resolve()}\" "
                 f"\"{args.pasta / 'falhas.txt'}\" \"{args.pasta}\""
                 + (f" --titulos \"{args.titulos}\"" if args.titulos else "")
                 + (f" --prefixo \"{args.prefixo}\"" if args.prefixo else "")
                 + f" --paralelos {args.paralelos} --intervalo-conexao {args.intervalo_conexao}"
                 + (f" --conexao-mbps {args.conexao_mbps}" if velocidade_manual else "")
                 + f" --uso-conexao {args.uso_conexao}"
                 + (" --nao-esperar-conexao" if args.nao_esperar_conexao else ""))
        sys.exit(1)


if __name__ == "__main__":
    main()
