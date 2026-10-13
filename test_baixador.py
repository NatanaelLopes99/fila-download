import logging
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests
import baixador as b


CONTEUDO = bytes(range(256)) * 128


class Servidor(BaseHTTPRequestHandler):
    ativos = pico = 0
    trava = threading.Lock()

    def log_message(self, *args):
        pass

    def do_GET(self):
        with self.trava:
            type(self).ativos += 1
            type(self).pico = max(self.pico, self.ativos)
        try:
            if '/416' in self.path:
                self.send_response(416)
                total = len(CONTEUDO) if 'good' in self.path else len(CONTEUDO) + 1
                self.send_header('Content-Range', f'bytes */{total}')
                self.end_headers()
                return
            conteudo = CONTEUDO[::-1] if '/etag-mudou' in self.path else CONTEUDO
            etag = '"v2"' if '/etag-mudou' in self.path else '"v1"'
            inicio = int(self.headers.get('Range', 'bytes=0-')[6:-1])
            if '/etag' in self.path:
                if self.headers.get('If-Range') and self.headers['If-Range'] != etag:
                    if 'ignora' not in self.path:
                        inicio = 0
            self.send_response(206 if inicio else 200)
            self.send_header('Content-Length', str(len(conteudo) - inicio))
            if '/etag' in self.path:
                self.send_header('ETag', etag)
            if inicio:
                offset = inicio + 1 if '/bad-range' in self.path else inicio
                self.send_header('Content-Range', f'bytes {offset}-{len(CONTEUDO)-1}/{len(CONTEUDO)}')
            self.end_headers()
            time.sleep(0.08)
            dados = conteudo[inicio:]
            if '/truncate' in self.path:
                dados = dados[:len(dados) // 2]
            for pos in range(0, len(dados), 4096):
                self.wfile.write(dados[pos:pos+4096])
                self.wfile.flush()
                time.sleep(0.005)
        except OSError:
            pass
        finally:
            with self.trava:
                type(self).ativos -= 1


class TestBaixador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b.log.addHandler(logging.NullHandler())
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Servidor)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        progresso = patch.object(b, 'tqdm', None)
        progresso.start()
        self.addCleanup(progresso.stop)
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.pasta = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def lote(self, quantidade=12, paralelos=5):
        itens = [b.Item(i, i, f'{self.url}/normal/{i}.mp4', 'Temporada')
                 for i in range(1, quantidade+1)]
        return itens, b.executar_lote(itens, self.pasta, 3, {}, '', 1, 0, paralelos)

    def test_titulos_numerados(self):
        arq = self.pasta / 'titulos.txt'
        arq.write_text('1\tVoltando Para Casa\n2   A Akatsuki\n3;Os Resultados\n', encoding='utf-8')
        titulos = b.ler_titulos(arq)
        self.assertEqual(titulos, {1: 'Voltando Para Casa', 2: 'A Akatsuki', 3: 'Os Resultados'})
        nome = b.nome_destino(b.Item(1, 1, self.url+'/1.mp4', ''), 3, titulos, 'Naruto Shippuden')
        self.assertEqual(nome, 'Naruto Shippuden 001 - Voltando Para Casa.mp4')
        arq.write_text('1\tUm\n1\tOutro\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            b.ler_titulos(arq)

    def test_paralelismo_e_integridade(self):
        for paralelos in (1, 5, 10):
            with self.subTest(paralelos=paralelos):
                ativos = pico = 0
                trava = threading.Lock()
                baixar = b.baixar

                def contar(*args, **kwargs):
                    nonlocal ativos, pico
                    with trava:
                        ativos += 1
                        pico = max(pico, ativos)
                    try:
                        return baixar(*args, **kwargs)
                    finally:
                        with trava:
                            ativos -= 1

                pasta = self.pasta / str(paralelos)
                itens = [b.Item(i, i, f'{self.url}/{i}.mp4', '') for i in range(1, 13)]
                with patch.object(b, 'baixar', contar):
                    resultado = b.executar_lote(itens, pasta, 3, {}, '', 1, 0, paralelos)
                self.assertEqual(resultado, (12, 0, []))
                self.assertLessEqual(pico, paralelos)
                self.assertGreaterEqual(pico, min(2, paralelos))
                for arq in pasta.glob('*.mp4'):
                    self.assertEqual(arq.read_bytes(), CONTEUDO)
                self.assertEqual(len(list(pasta.glob('*.mp4'))), 12)
                self.assertEqual(b.executar_lote(itens, pasta, 3, {}, '', 1, 0, paralelos), (0, 12, []))

    def test_retomada(self):
        destino = self.pasta / 'retomado.mp4'
        destino.with_name(destino.name+'.part').write_bytes(CONTEUDO[:123])
        with requests.Session() as sessao:
            b.baixar(sessao, self.url+'/resume.mp4', destino, 1, progresso=False)
        self.assertEqual(destino.read_bytes(), CONTEUDO)

    def test_incompleto_nao_e_finalizado(self):
        destino = self.pasta / 'incompleto.mp4'
        with requests.Session() as sessao, self.assertRaises(b.ErroTemporario):
            b.baixar(sessao, self.url+'/truncate.mp4', destino, 1, progresso=False)
        self.assertFalse(destino.exists())
        self.assertTrue(destino.with_name(destino.name+'.part').exists())

    def test_416_valida_tamanho(self):
        for tipo in ('good', 'bad'):
            destino = self.pasta / (tipo+'.mp4')
            destino.with_name(destino.name+'.part').write_bytes(CONTEUDO)
            with requests.Session() as sessao:
                if tipo == 'good':
                    b.baixar(sessao, self.url+'/416-good.mp4', destino, 1, progresso=False)
                    self.assertEqual(destino.read_bytes(), CONTEUDO)
                else:
                    with self.assertRaises(b.ErroDefinitivo):
                        b.baixar(sessao, self.url+'/416-bad.mp4', destino, 1, progresso=False)
                    self.assertFalse(destino.exists())

    def test_retomada_invalida(self):
        destino = self.pasta / 'bad.mp4'
        parcial = destino.with_name(destino.name+'.part')
        parcial.write_bytes(CONTEUDO[:123])
        with requests.Session() as sessao, self.assertRaises(b.ErroDefinitivo):
            b.baixar(sessao, self.url+'/bad-range.mp4', destino, 1, progresso=False)
        self.assertFalse(destino.exists())
        self.assertEqual(parcial.read_bytes(), CONTEUDO[:123])

    def test_interrupcao_salva_pendentes(self):
        def interromper(futuros):
            limite = time.monotonic() + 3
            while time.monotonic() < limite:
                if any(arq.stat().st_size > 0 for arq in self.pasta.rglob('*.part')):
                    break
                time.sleep(0.001)
            raise KeyboardInterrupt
        with patch.object(b, 'as_completed', interromper), patch.object(b, 'CHUNK', 4096):
            itens, (ok, pulados, falhas) = self.lote()
        self.assertEqual(ok + pulados + len(falhas), len(itens))
        self.assertGreater(len(falhas), 0)
        self.assertTrue(any(arq.stat().st_size > 0 for arq in self.pasta.rglob('*.part')))
        b.salvar_falhas(self.pasta, falhas)
        self.assertEqual(b.ler_links(self.pasta/'falhas.txt', False), [it for it, _ in falhas])

    def test_erro_fatal_para_lote(self):
        with patch.object(b, 'baixar', side_effect=b.ErroFatal('disco cheio')):
            itens, (ok, pulados, falhas) = self.lote()
        self.assertEqual(ok + pulados + len(falhas), len(itens))
        self.assertEqual(ok, 0)
        self.assertTrue(any(motivo == 'disco cheio' for _, motivo in falhas))

    def test_destinos_repetidos_serializam(self):
        Servidor.pico = 0
        itens = [b.Item(i, i, f'{self.url}/igual.mp4?q={i}', '') for i in range(1, 4)]
        resultado = b.executar_lote(itens, self.pasta, 3, {}, '', 1, 0, 5)
        self.assertEqual(resultado, (1, 2, []))
        self.assertEqual(Servidor.pico, 1)

    def test_queda_nao_gasta_tentativa_e_retoma_parcial(self):
        destino = self.pasta / 'queda.mp4'
        parar = threading.Event()
        respostas = iter((False, False, True, False, True, True))
        monitor = b.MonitorConexao(parar, 0.001, lambda: next(respostas))
        chamadas = 0

        def transferir(sessao, url, dest, parcial, *args):
            nonlocal chamadas
            chamadas += 1
            if chamadas == 1:
                parcial.write_bytes(CONTEUDO[:123])
                raise requests.ConnectionError('roteador caiu')
            self.assertEqual(parcial.read_bytes(), CONTEUDO[:123])
            with parcial.open('ab') as arquivo:
                arquivo.write(CONTEUDO[123:])

        with patch.object(b, 'baixar_uma_vez', transferir):
            b.baixar(None, self.url+'/queda.mp4', destino, 1, parar, False, monitor)
        self.assertEqual(chamadas, 2)
        self.assertEqual(destino.read_bytes(), CONTEUDO)
        self.assertEqual(monitor.geracao, 1)

    def test_queda_compartilhada_por_cinco_downloads(self):
        barreira = threading.Barrier(5, timeout=3)
        trava = threading.Lock()
        chamadas = {}
        respostas = iter((False, False, True, False, True, True))

        def transferir(sessao, url, destino, parcial, *args):
            with trava:
                numero = chamadas.get(url, 0) + 1
                chamadas[url] = numero
            if numero == 1:
                parcial.write_bytes(CONTEUDO[:123])
                barreira.wait()
                raise requests.Timeout('sem conexão')
            with parcial.open('ab') as arquivo:
                arquivo.write(CONTEUDO[123:])

        with patch.object(b.MonitorConexao, 'testar_internet',
                          side_effect=lambda: next(respostas, True)) as testar, \
             patch.object(b, 'baixar_uma_vez', transferir), \
             self.assertLogs(b.log, level='INFO') as registro:
            itens = [b.Item(i, i, self.url+f'/queda-{i}.mp4', '') for i in range(1, 6)]
            resultado = b.executar_lote(itens, self.pasta, 3, {}, '', 1, 0, 5,
                                       intervalo_conexao=0.001)
        self.assertEqual(resultado, (5, 0, []))
        self.assertEqual(testar.call_count, 6)
        self.assertEqual(sum('Internet indisponível' in linha for linha in registro.output), 1)
        self.assertEqual(list(chamadas.values()), [2]*5)
        for arquivo in self.pasta.glob('*.mp4'):
            self.assertEqual(arquivo.read_bytes(), CONTEUDO)

    def test_erro_do_servidor_com_internet_conta_tentativa(self):
        parar = threading.Event()
        monitor = b.MonitorConexao(parar, 0.001, lambda: True)
        with patch.object(b, 'baixar_uma_vez', side_effect=requests.ConnectionError('servidor fora')) as baixar:
            with self.assertRaises(b.ErroTemporario):
                b.baixar(None, self.url+'/servidor.mp4', self.pasta/'servidor.mp4',
                         1, parar, False, monitor)
        self.assertEqual(baixar.call_count, 1)

    def test_ctrl_c_durante_espera_de_conexao(self):
        parar = threading.Event()
        monitor = b.MonitorConexao(parar, 0.02, lambda: False)
        timer = threading.Timer(0.01, parar.set)
        timer.start()
        try:
            with self.assertRaises(b.ErroInterrompido):
                monitor.apos_erro(0)
            with self.assertRaises(b.ErroInterrompido):
                monitor.aguardar()
        finally:
            timer.join()

    def test_sonda_tenta_segundo_destino(self):
        monitor = b.MonitorConexao(threading.Event())
        from unittest.mock import MagicMock
        with patch.object(b.socket, 'create_connection',
                          side_effect=[OSError('DNS falhou'), MagicMock()]) as conectar:
            self.assertTrue(monitor.testar_internet())
        self.assertEqual(conectar.call_count, 2)

    def test_calculo_do_percentual_da_conexao(self):
        self.assertEqual(b.calcular_limite(100, 80), 10_000_000)
        self.assertEqual(b.calcular_limite(100, 1), 125_000)
        self.assertEqual(b.calcular_limite(100, 100), 12_500_000)
        for mbps, percentual in ((0, 80), (-1, 80), (float('nan'), 80),
                                 (float('inf'), 80), (100, 0), (100, 101)):
            with self.subTest(mbps=mbps, percentual=percentual), self.assertRaises(ValueError):
                b.calcular_limite(mbps, percentual)

    def test_reservas_compartilhadas_e_reinicio_apos_pausa(self):
        limitador = b.LimitadorVelocidade(1000, threading.Event())
        with patch.object(b.time, 'monotonic', return_value=10):
            self.assertAlmostEqual(limitador.reservar(1000), 1)
            self.assertAlmostEqual(limitador.reservar(1000), 2)
            self.assertAlmostEqual(limitador.reservar(1000), 3)
        # Não acumula crédito para uma rajada após ficar parado.
        with patch.object(b.time, 'monotonic', return_value=100):
            self.assertAlmostEqual(limitador.reservar(1000), 1)

    def test_limite_total_com_cinco_downloads(self):
        itens = [b.Item(i, i, self.url+f'/limitado-{i}.mp4', '') for i in range(1, 6)]
        inicio = time.monotonic()
        resultado = b.executar_lote(itens, self.pasta, 3, {}, '', 1, 0, 5,
                                   limite_bytes=65536)
        duracao = time.monotonic() - inicio
        self.assertEqual(resultado, (5, 0, []))
        # 5 x 32768 bytes / 65536 bytes/s = pelo menos 2,5 segundos no total.
        self.assertGreaterEqual(duracao, 2.45)
        for arquivo in self.pasta.glob('*.mp4'):
            self.assertEqual(arquivo.read_bytes(), CONTEUDO)

    def test_ctrl_c_durante_limite_de_velocidade(self):
        parar = threading.Event()
        limitador = b.LimitadorVelocidade(1024, parar)
        timer = threading.Timer(0.01, parar.set)
        timer.start()
        try:
            inicio = time.monotonic()
            with self.assertRaises(b.ErroInterrompido):
                limitador.aguardar(1024 * 1024)
            self.assertLess(time.monotonic() - inicio, 1)
        finally:
            timer.join()

    def test_cli_percentual_padrao_e_personalizado(self):
        links = self.pasta / 'links.txt'
        links.write_text(self.url+'/1.mp4\n', encoding='utf-8')
        for extras, esperado in (([], 10_000_000), (['--uso-conexao', '50'], 6_250_000)):
            argumentos = ['baixador.py', str(links), str(self.pasta/'destino'),
                          '--conexao-mbps', '100'] + extras
            with patch.object(b.sys, 'argv', argumentos), \
                 patch.object(b, 'configurar_log'), \
                 patch.object(b, 'executar_lote', return_value=(1, 0, [])) as executar:
                b.main()
            self.assertEqual(executar.call_args.args[-1], esperado)

    def test_cli_mede_velocidade_quando_omitida(self):
        links = self.pasta / 'links.txt'
        links.write_text(self.url+'/1.mp4\n', encoding='utf-8')
        argumentos = ['baixador.py', str(links), str(self.pasta/'destino')]
        with patch.object(b.sys, 'argv', argumentos), \
             patch('builtins.input') as perguntar, \
             patch.object(b, 'medir_velocidade', return_value=100.5) as medir, \
             patch.object(b, 'configurar_log'), \
             patch.object(b, 'executar_lote', return_value=(1, 0, [])) as executar:
            b.main()
        perguntar.assert_not_called()
        medir.assert_called_once()
        self.assertEqual(executar.call_args.args[-1], b.calcular_limite(100.5, 80))

    def test_medicao_automatica_calcula_banda_e_limita_volume(self):
        principal = threading.current_thread()
        tempos = iter((10, 12))

        def relogio():
            return next(tempos) if threading.current_thread() is principal else 11

        with patch.object(b.time, 'monotonic', relogio):
            velocidade = b.medir_velocidade(max_bytes=32768, url=self.url+'/medicao')
        self.assertAlmostEqual(velocidade, 32768 * 8 / 2 / 1_000_000)

    def test_medicao_sem_conexao_nao_inventa_velocidade(self):
        with patch.object(b.requests, 'Session', side_effect=requests.ConnectionError('offline')):
            with self.assertRaises(b.ErroTemporario):
                b.medir_velocidade()

    def test_medicao_cancelada_nao_conecta(self):
        parar = threading.Event()
        parar.set()
        with patch.object(b.requests, 'Session') as sessao:
            with self.assertRaises(b.ErroInterrompido):
                b.medir_velocidade(parar=parar)
        sessao.assert_not_called()

    def test_cli_falha_de_medicao_nao_inicia_downloads(self):
        links = self.pasta / 'links.txt'
        links.write_text(self.url+'/1.mp4\n', encoding='utf-8')
        argumentos = ['baixador.py', str(links), str(self.pasta/'destino')]
        with patch.object(b.sys, 'argv', argumentos), \
             patch.object(b, 'configurar_log'), \
             patch.object(b, 'medir_velocidade', side_effect=b.ErroTemporario('teste indisponível')), \
             patch.object(b.MonitorConexao, 'apos_erro', return_value=False), \
             patch.object(b, 'executar_lote') as executar:
            with self.assertRaises(SystemExit) as saida:
                b.main()
        self.assertIn('--conexao-mbps', str(saida.exception))
        executar.assert_not_called()

    def preparar_parcial_com_etag(self, url, destino, etag='"v1"'):
        import hashlib
        import json
        parcial = destino.with_name(destino.name+'.part')
        parcial.write_bytes(CONTEUDO[:123])
        dados = {'origem': hashlib.sha256(url.encode()).hexdigest(), 'etag': etag,
                 'tamanho': len(CONTEUDO)}
        b.caminho_metadados(parcial).write_text(json.dumps(dados), encoding='utf-8')
        return parcial

    def test_retomada_com_etag_preserva_dados_e_remove_metadados(self):
        destino = self.pasta / 'etag.mp4'
        url = self.url+'/etag.mp4'
        parcial = self.preparar_parcial_com_etag(url, destino)
        with requests.Session() as sessao, patch.object(sessao, 'get', wraps=sessao.get) as get:
            b.baixar(sessao, url, destino, 1, progresso=False)
        self.assertEqual(get.call_args.kwargs['headers']['If-Range'], '"v1"')
        self.assertEqual(destino.read_bytes(), CONTEUDO)
        self.assertFalse(b.caminho_metadados(parcial).exists())

    def test_arquivo_mudou_reinicia_sem_misturar_conteudo(self):
        for rota in ('/etag-mudou.mp4', '/etag-mudou-ignora.mp4'):
            with self.subTest(rota=rota):
                destino = self.pasta / Path(rota).name
                url = self.url+rota
                self.preparar_parcial_com_etag(url, destino)
                with requests.Session() as sessao:
                    b.baixar(sessao, url, destino, 1, progresso=False)
                self.assertEqual(destino.read_bytes(), CONTEUDO[::-1])

    def test_validador_fraco_nao_e_usado_em_if_range(self):
        self.assertEqual(b.validador_metadados({'etag': 'W/"v1"'}), (None, None))
        self.assertEqual(b.validador_metadados({'last_modified': 'data inválida'}), (None, None))
        self.assertEqual(b.validador_metadados({
            'last_modified': 'Wed, 01 Jan 2020 00:00:00 GMT',
            'date': 'Wed, 01 Jan 2020 00:02:00 GMT',
        }), ('Last-Modified', 'Wed, 01 Jan 2020 00:00:00 GMT'))

    def test_opcoes_de_tempo_rejeitam_nan_e_infinito(self):
        from io import StringIO
        for opcao, valor in (('--pausa', 'nan'), ('--pausa', 'inf'),
                             ('--intervalo-conexao', 'nan'), ('--intervalo-conexao', 'inf')):
            with self.subTest(opcao=opcao, valor=valor), \
                 patch.object(b.sys, 'argv', ['baixador.py', 'links.txt', 'destino', opcao, valor]), \
                 patch.object(b.sys, 'stderr', StringIO()):
                with self.assertRaises(SystemExit) as saida:
                    b.main()
            self.assertEqual(saida.exception.code, 2)

    def test_link_mudou_nao_aproveita_parcial_de_outra_origem(self):
        destino = self.pasta / 'novo.mp4'
        self.preparar_parcial_com_etag(self.url+'/antigo.mp4', destino)
        url = self.url+'/etag-mudou.mp4'
        with requests.Session() as sessao, patch.object(sessao, 'get', wraps=sessao.get) as get:
            b.baixar(sessao, url, destino, 1, progresso=False)
        self.assertNotIn('Range', get.call_args.kwargs['headers'])
        self.assertEqual(destino.read_bytes(), CONTEUDO[::-1])


if __name__ == '__main__':
    unittest.main()
