# Wan Studio

**Troca de pessoa em vídeo**, no estilo Higgsfield, com o **Wan 2.2 Animate** (open source): a
pessoa de uma foto entra no lugar da pessoa de um vídeo, com os mesmos movimentos, o mesmo
cenário e o áudio original. O site é um arquivo só, a sua galeria fica guardada no navegador e
a GPU é emprestada, de um de dois lugares.

| GPU | Custo | Troca de pessoa de 23 s na melhor qualidade | Como liga |
|---|---|---|---|
| **Gratuita** (Kaggle, 2× T4) | nenhum; 30 horas por semana | 2 h 10 (medido em 13/10/2026) | **Ligar GPU** |
| **Rápida** (Modal, H100) | os US$ 30 de crédito que o Modal dá por mês; exige cartão cadastrado | uns 10 minutos (conta, ainda sem medida) | **Ligar GPU rápida**, ou o botão do próprio site |

O site é o mesmo nas duas, e acha sozinho a que estiver ligada.

O Wan Studio também gera vídeo a partir de texto e de imagem (veja *Os outros modos*), mas
desde 13/10/2026 **a sessão de costume só faz a troca de pessoa**: baixa 27 GB de modelos em
vez de 83, deixa as duas GPUs do Kaggle para o vídeo e o site abre direto nela, sem as abas
dos outros modos.

```
 SITE (endereço fixo)                          GPU GRATUITA (só enquanto você usa)
 site/index.html  ──── https + senha ────────> notebook do Kaggle
 · formulário, presets, galeria                · Wan Studio (API e fila)
 · vídeos guardados no navegador               · ComfyUI + Wan 2.2
        ▲                                              │
        └────── anúncio cifrado (ntfy.sh) ─────────────┘
                "a sessão de hoje está neste endereço"
```

O site abre sempre no mesmo lugar e funciona com a GPU desligada (você vê e baixa o que já
gerou). Para gerar, você liga a GPU com dois cliques; o site encontra a sessão sozinho.

## Ligar a GPU rápida (Modal)

A T4 gratuita leva 154 segundos em cada passo de um pedaço de vídeo (medido: é a placa, não a
configuração), e o Kaggle só dá duas. Para a troca de pessoa sair em minutos é preciso uma
placa umas dez vezes mais rápida. O [Modal](https://modal.com) aluga essas placas por segundo
e dá **US$ 30 de crédito por mês**, renovados, no plano gratuito; o Wan Studio liga a máquina
só enquanto você usa e a desliga sozinho.

> **Estado em 13/10/2026: pronto e nunca rodado de verdade.** Tudo o que não depende da conta
> foi testado (a sessão de verdade sobe aqui, com o ComfyUI simulado, pelas mãos de um Modal de
> mentira: 33 verificações). O que só a conta mostra continua sem conferir: a imagem montar, os
> modelos baixarem, a placa gerar, e **os tempos e custos abaixo, que são contas** a partir da
> ficha das placas. Faltava uma conta com cartão cadastrado.

### Antes da primeira vez

1. **Conta no Modal**, em https://modal.com (entra com o Google ou o GitHub).
2. **Cartão cadastrado**, em https://modal.com/settings/usage. O Modal exige um meio de
   pagamento para ligar máquinas, mesmo dentro do crédito gratuito.
3. Na mesma página, ponha o **spend limit** (o teto do que pode ser cobrado além do crédito)
   no menor valor que ela aceitar. Com ele, quando o crédito do mês acaba as máquinas param
   em vez de o cartão ser cobrado.

### Ligar

Dois cliques em **Ligar GPU rápida** (nesta pasta). Na primeira vez ele:

1. instala o cliente do Modal numa pasta própria (`nuvem/.venv`) e abre o navegador para você
   autorizar este computador (*Approve*);
2. publica o Wan Studio na sua conta. O Modal monta a imagem (ComfyUI e PyTorch): uns 8 minutos,
   uma vez;
3. baixa os modelos da troca de pessoa (36 GB) para um volume da sua conta: de 5 a 15 minutos,
   uma vez, sem usar GPU nem a internet do seu computador;
4. liga a máquina e abre o site já pareado.

Daí em diante ligar leva **1 a 2 minutos**: os modelos já estão no volume. E não precisa mais
do atalho: o site ganha o botão **Ligar a GPU rápida** (clique no indicador *GPU desligada*),
que funciona de qualquer aparelho em que o site tenha sido aberto por esse atalho uma vez.

A máquina **se desliga sozinha após 5 minutos sem uso** (`--ocioso` muda isso) e só cobra
enquanto está ligada. Para desligar antes: no site, **indicador verde → Desligar GPU**.

```powershell
python nuvem\ligar.py status        # ligada ou não, a placa e o crédito gasto no mês (estimativa)
python nuvem\ligar.py registro      # o que a máquina está fazendo
python nuvem\ligar.py desligar      # desliga agora
python nuvem\ligar.py --gpu L40S    # outra placa (L40S, A100, H100, H200, B200)
python nuvem\ligar.py modelos --tudo   # baixa também os modelos do 5B e do 14B (mais 50 GB)
python nuvem\ligar.py publicar      # reenvia o código (o atalho já faz isso quando ele muda)
```

### Quanto custa

O crédito mensal paga o tempo de máquina. Preços do Modal em 13/10/2026, por hora ligada (a
máquina cobra também processador e memória: uns US$ 0,45 por hora, já somados na última coluna):

| Placa | Memória | Só a placa | A sessão | Horas com os US$ 30 |
|---|---|---|---|---|
| L40S | 48 GB | 1,95 | 2,40 | 12,5 |
| A100 | 40 GB | 2,10 | 2,55 | 11,8 |
| H100 (padrão) | 80 GB | 3,95 | 4,40 | 6,8 |
| H200 | 141 GB | 4,54 | 4,99 | 6,0 |
| B200 | 180 GB ou mais | 6,25 | 6,70 | 4,5 |

Pela conta (a H100 gerando umas 10 vezes mais rápido que a T4), um vídeo de 23 s na melhor
qualidade sairia em uns 10 minutos e US$ 0,70: uns 40 por mês dentro do crédito. Na qualidade
*Boa*, uns 5 minutos e 80 por mês. O site mostra, antes de gerar, o tempo estimado e quanto
aquele vídeo gasta; e, em cada vídeo pronto, quanto ele gastou de fato. Guardar os modelos não
gasta crédito (o Modal dá 1 TiB de volume), nem baixar os vídeos.

### O que muda em relação ao Kaggle

- **Uma placa só**, grande. A troca de pessoa usa o modelo em fp8 (em vez do GGUF Q4 da T4) e
  ganha a qualidade **Ultra** (720p direto); o melhorador de prompt, que usa a segunda GPU do
  Kaggle, não fica disponível.
- **Os modelos ficam guardados** no volume da conta: nada é baixado de novo a cada sessão.
- **O Modal pode tomar a máquina de volta** no meio de um vídeo (é raro; acontece mais em
  sessões longas). A sessão sobe de novo sozinha, mas o vídeo que estava sendo gerado se
  perde: é só pedir outra vez.
- O endereço público é um túnel do próprio Modal (no Kaggle, da Cloudflare). O limite de 100 MB
  por vídeo do site continua valendo.

## Ligar a GPU gratuita (Kaggle)

Dois cliques em **Ligar GPU** (nesta pasta). Ele envia o notebook ao Kaggle pela API oficial,
que o executa em segundo plano, e abre o site já pareado. Em uns 5 minutos o indicador do
site fica verde sozinho, e a troca de pessoa é liberada em mais 3 (tempos da sessão antiga, que
baixava o 5B antes; a de hoje, só de troca, conectou em 3 minutos e meio e liberou a troca em 5
numa sessão de CPU do Kaggle, e na de GPU ainda não foi cronometrada). Não é preciso abrir o
Kaggle nem deixar janela nenhuma aberta. Em horários cheios o Kaggle põe a sessão numa fila
antes de ligar a máquina: num teste de manhã foram 35 minutos, de madrugada nenhum. O site
conecta quando ela sair da fila.

Para desligar: no site, **indicador verde → Desligar GPU**. Se esquecer, a sessão se encerra
sozinha após 60 minutos sem uso.

### Antes da primeira vez

1. **Conta no Kaggle com telefone verificado.** Crie a conta em https://www.kaggle.com e, em
   https://www.kaggle.com/settings, confirme o número em *Phone Verification*. É gratuito e é
   obrigatório: sem isso o Kaggle entrega a sessão sem internet e sem GPU, e nada funciona.
2. **Token da conta.** Na primeira vez o Ligar GPU abre https://www.kaggle.com/settings/api:
   clique em *Generate New Token*, copie e cole na janela. O token fica guardado em
   `free/kaggle_token.txt`. Quem tiver esse arquivo usa a sua conta do Kaggle: não o
   compartilhe, e apague-o (e gere outro token) se suspeitar que vazou.

O Ligar GPU confere o começo da execução e avisa se a sessão parar logo (o caso típico é o
telefone não verificado). Dois comandos ajudam a ver o que está acontecendo:

```powershell
python free\ligar.py status      # ligada, na fila ou desligada, e a cota de GPU da semana
python free\ligar.py registro    # o que a GPU está fazendo agora
```

### Sem o atalho, pelo site do Kaggle

1. No Kaggle: *Create → New Notebook → File → Import Notebook* e envie
   `free/wan_studio_free.ipynb`. **Deixe o notebook privado.**
2. No painel direito, em *Session options*: **Accelerator = GPU T4 x2** e **Internet = On**.
3. Clique em **Run All**. Em uns 5 minutos a segunda célula mostra o **código de conexão**.
4. No site, clique em **Ligar**, abra *Ligar pelo site do Kaggle*, cole o código e clique em
   **Conectar**. Só é preciso na primeira vez em cada navegador.

## Placa maior: Lightning AI (opcional)

A T4 do Kaggle não gera 720p direto em tempo razoável. O [Lightning AI](https://lightning.ai)
dá créditos gratuitos que pagam horas de placas maiores (L4 de 24 GB, L40S de 48 GB), e nelas a
troca de pessoa ganha a qualidade **Ultra** (720p direto). Dois cliques em
**Ligar GPU (Lightning)**: ele mostra o saldo, pergunta a placa, liga a máquina, envia o Wan
Studio para ela e abre o mesmo site, já pareado. O Kaggle continua valendo para rascunhos.

> **Estado em 12/10/2026: não é gratuito de verdade, e ficou de lado.** Numa conta nova, real,
> o Lightning deu **5 créditos, uma vez só**, sem renovação marcada (mais 25 só cadastrando
> cartão), e 10 GB de disco guardado. Com a L40S a 2,14 créditos por hora na nuvem mais barata,
> isso são umas 2 horas de máquina no total. O gratuito que se renova é o do Kaggle.
>
> O que foi conferido nessa conta: o login pelo navegador, o saldo, a criação do Studio, ligar
> e desligar uma máquina (de CPU), os comandos remotos e o envio do pacote. **Nunca rodou com
> placa de vídeo**: a instalação na imagem deles (Python 3.14, PyTorch com CUDA 13) e a
> qualidade *Ultra* seguem sem teste. O resto desta seção descreve o que o atalho faz.

### Antes da primeira vez

1. **Conta no Lightning AI**, em https://lightning.ai, com o telefone verificado (é o que
   libera os créditos gratuitos; não pede cartão).
2. **A autorização da conta.** Na primeira vez o atalho abre o Lightning no seu navegador
   (entre na conta, se ele pedir) e recebe sozinho a chave pessoal, como faz a ferramenta
   oficial; não há nada para copiar. Se isso não funcionar, ele pede a *API Key* (no site, sua
   foto → *Global Settings* → *Keys*); essa chave pode não ter permissão para ligar máquinas, e
   aí o atalho avisa. O que vier fica guardado em `lightning/lightning_chave.txt`. Quem tiver
   esse arquivo usa a sua conta e os seus créditos: não o compartilhe.

### O que muda em relação ao Kaggle

- **Gasta créditos enquanto ligada.** O atalho mostra o saldo e o preço por hora de cada placa
  antes de ligar. Para não gastar à toa, a máquina se desliga sozinha após 15 minutos sem uso
  (no Kaggle são 60), e também quando a instalação falha.
- **Uma placa só**, maior. Os pedaços de uma troca saem um depois do outro, e o melhorador de
  prompt (que usa a segunda GPU do Kaggle) não fica disponível.
- **Os modelos podem ficar guardados.** Se o disco gratuito da conta comportar (60 GB ou mais),
  eles são baixados uma vez só; senão, vão para um disco temporário da máquina e são baixados
  a cada vez que ela liga.
- **Plano gratuito**: o Lightning reinicia a máquina a cada 4 horas. Uma troca em andamento
  nesse momento se perde.

```powershell
python lightning\ligar.py status      # ligada ou desligada, a placa, o saldo de créditos e o disco
python lightning\ligar.py registro    # o que a máquina está fazendo agora
python lightning\ligar.py desligar    # desliga a máquina (para de gastar créditos)
python lightning\ligar.py --gpu L4    # liga sem perguntar a placa (L40S, L4, T4, RTX6000, A100, H100)
python lightning\ligar.py --interrompivel   # máquina mais barata, que o Lightning pode tomar de volta
```

**Preços (lista pública do Lightning em 12/10/2026, em créditos por hora).** Dependem da nuvem
em que o Studio fica, e o atalho lê os da nuvem certa antes de ligar:

| Placa | Memória | Nuvem padrão (AWS) | Outras nuvens |
|---|---|---|---|
| T4 | 16 GB | 1,04 | 0,69 (GCP) |
| L4 | 24 GB | 1,68 | 0,79 (GCP) |
| L40S | 48 GB | 3,54 | 2,14 (Nebius) |
| RTX 6000 Pro | 96 GB | 4,64 | 5,39 (GCP) |
| A100 | 40 ou 80 GB | não há | 2,19 (Lambda, 40 GB); 5,68 (GCP, 80 GB) |
| H100 | 80 GB | não há | 4,68 (Lambda) a 12,34 (GCP) |

Com 15 créditos isso dá de 4 a 7 horas de L40S, ou de 9 a 19 de L4. A versão interrompível
custa de 30 a 45% menos onde existe.

## O que dá para fazer

A sessão de costume faz uma coisa: **trocar a pessoa de um vídeo** (a seção logo abaixo), com
a galeria, o *Comparar com o original* e a produção em massa.

### Os outros modos

Continuam no código e voltam, como abas no site, quando a sessão é ligada com os modelos deles:

- no Kaggle: `python free\build.py --tudo` e **Ligar GPU** de novo (a sessão passa a baixar os
  83 GB; `python free\build.py`, sem nada, volta à sessão só de troca);
- na GPU rápida: `python nuvem\ligar.py modelos --tudo`.

| Recurso | Como funciona |
|---|---|
| Texto → Vídeo | Wan 2.2 5B (24 fps) ou 14B (16 fps), 3 ou 5 segundos, 480p ou 720p |
| Imagem → Vídeo | Anima uma imagem enviada; a proporção acompanha a imagem |
| Início + Fim | Gera a transição entre duas imagens (precisa do 14B) |
| Imagem | Gera uma imagem parada, sempre com os 20 passos; o botão **Animar** a leva para Imagem → Vídeo |
| Trocar pessoa | Põe a pessoa de uma imagem no lugar da pessoa de um vídeo de até 60 s (Wan 2.2 Animate) |
| Continuar | Pega o último frame de um vídeo e começa o próximo a partir dele |
| 36 movimentos de câmera | Dolly, crash zoom, órbita, grua, FPV, bullet time, dolly zoom, whip pan… |
| 21 efeitos | Explosão, em chamas, desintegrar, levitação, congelar, raios, holograma… |
| 16 estilos | Cinemático, noir, cyberpunk, anime, massinha, pixel art, VHS… |
| Turbo | Vídeo em poucos passos em vez de 20: 3 no 5B (FastWan), 4 no 14B (lightx2v) |
| Melhorar com IA | Traduz o prompt para inglês e o detalha (um modelo Qwen3 na segunda GPU) |
| Lote | 1, 2 ou 4 variações de uma vez, com seeds diferentes |
| Galeria | Guardada no navegador; baixar, reusar (prompt, seed e imagens), excluir |

Câmeras, efeitos e estilos são trechos de prompt somados ao seu texto (`app/presets.py`). O
Wan 2.2 segue bem linguagem de câmera, mas o resultado não é idêntico aos presets do
Higgsfield, que usam modelos proprietários. Para criar um preset, acrescente uma linha nesse
arquivo e rode `python free/build.py`. Descreva o movimento com palavras comuns: o modelo lê o
texto ao pé da letra, e num teste "Crane up" pôs um guindaste na cena.

Exemplos gerados na GPU gratuita estão na pasta `exemplos`. Os da troca de pessoa feitos entre
11 e 12/10/2026, quando ela saía com defeito, estão separados numa subpasta, e uma imagem
compara os dois resultados.

### Trocar a pessoa de um vídeo

Envie uma imagem da pessoa e um vídeo (até 100 MB). O resultado é o
mesmo vídeo com a pessoa da imagem no lugar da pessoa principal: mesmos movimentos e expressões,
mesmo cenário, luz ajustada à do vídeo e o áudio original. A troca é da pessoa inteira (rosto,
cabelo, corpo e roupa vêm da imagem), não só do rosto.

- **A imagem**: a pessoa bem visível e iluminada. Funciona melhor com enquadramento e proporção
  parecidos com os do vídeo (em pé se o vídeo é em pé; corpo inteiro se o vídeo mostra o corpo
  inteiro). Quando a proporção difere, a imagem entra inteira com faixas pretas e a pessoa
  fica menor: num teste com imagem quadrada e vídeo em pé, o gorro de um personagem de corpo
  inteiro não foi copiado (com a foto quadrada de uma pessoa de meio corpo, saiu certo).
- **O vídeo**: o trecho deve começar com a pessoa de frente e com algum cenário à vista em
  volta dela, porque é pelo começo que a geração começa (ajuste **Começar em** para isso). Num
  teste em que ela começava de costas e enchia o quadro, sem cenário para o modelo se apoiar,
  ele inventou uma mesa na borda de baixo; o mesmo vídeo a partir de 1,5 s, com ela já de
  frente, saiu limpo. O recorte da pessoa é refeito a cada pedaço, a partir da pose em vários
  quadros dele. Com várias pessoas, é trocada a maior delas.
- **Duração**: 5, 10, 15, 30 ou 60 segundos, a partir do instante em **Começar em**. O vídeo é
  convertido para o tamanho de geração (deitado, em pé ou quadrado, conforme o original).
- **Qualidade**: muda o tamanho em que o modelo gera. Em todas ele gera os 16 quadros por
  segundo do vídeo. O formulário mostra o tempo estimado.

  | Qualidade | O que o modelo gera | Arquivo | 10 s | 30 s | 60 s |
  |---|---|---|---|---|---|
  | Rápida | 59% dos pixels do 480p, ampliados no fim | 480p, 16 fps | 14 min | 38 min | 1 h 20 |
  | Boa (padrão) | 480p | 480p, 16 fps | 30 min | 1 h 30 | 3 h |
  | Máxima | 576p (44% mais pixels) | 576p, 16 fps | 55 min | 2 h 40 | 5 h 25 |
  | Ultra | direto em 720p, com 6 passos | 720p, 16 fps | não medido | | |

  Tempos no Kaggle. Foram medidos, em 12/10/2026, 9,3 s na *Boa* (26,6 min) e na *Rápida*
  (11,6 min); os demais saem da mesma conta (tempo por pedaço × número de pedaços), e os da
  *Máxima* usam uma medida antiga, de um pedaço de 3 s. A *Rápida* sai coerente do começo ao
  fim, com menos detalhe (num teste, a estampa de uma manga saiu simplificada). A *Ultra* só
  aparece numa placa de 24 GB ou mais (veja *Placa maior: Lightning AI*). Não há 720p direto
  na T4: um vídeo de 10 s levava mais de 3 horas.
- **Alta definição**: depois de gerados, os pedaços passam por um modelo de restauração de
  vídeo (SeedVR2), que recria detalhe de pele, cabelo e tecido, e o arquivo sai com 720 px no
  lado menor. Vale para as qualidades abaixo da *Ultra* (que já sai em 720p). É bem mais
  nítida e custa caro: num vídeo em pé de 5 s (720×1248), medido em 12/10/2026, a geração levou
  12 min e a restauração mais 15. Conte com o dobro do tempo.
- **Movimento fluido**: o arquivo sai com 32 quadros por segundo em vez de 16; os quadros a mais
  são calculados entre os gerados (RIFE), em segundos, depois da geração. Gerar 15 por segundo
  para sair com 30 exatos foi testado e descartado: a troca piorou (veja *O que foi
  verificado*).
- **Manter o cenário do vídeo**: desmarque para o contrário: a pessoa da imagem copia os
  movimentos, mas fica no cenário da própria imagem.
- O prompt é opcional; presets de câmera, efeito e estilo não se aplicam.
- **Comparar com o original**: no vídeo pronto (clique nele na galeria), esse botão põe o vídeo
  de origem e o resultado lado a lado, andando juntos. Vale para vídeos de origem de até 25 MB,
  que são os que o navegador guarda.

Use apenas imagens de pessoas que autorizaram o uso.

**Como um vídeo longo é gerado.** O vídeo é cortado em pedaços de até 77 quadros (4,8 s). Um
pedaço gerado sozinho inventa detalhes próprios (num teste, o cabelo da personagem mudou de um
pedaço para o outro), então cada pedaço continua do anterior: recebe 5 quadros dele e segue
dali, do começo do vídeo até o fim. No fim o servidor emenda tudo, iguala a cor entre os
pedaços e devolve o áudio. O card mostra "pedaço 2 de 3" e o tempo restante.

**Por que uma GPU fica parada durante uma troca.** O Kaggle dá duas, e por um tempo o estúdio
usou as duas no mesmo vídeo: começava por um pedaço curto no meio e crescia para os dois
lados, e ainda gerava só 8 ou 12 quadros por segundo e calculava os outros. Ficou de 2 a 3
vezes mais rápido e bem pior: a pessoa saía com objetos inventados, o cabelo da pessoa
original e a cor estourada. As medidas estão em *O que foi verificado*. Hoje a segunda GPU
serve para um segundo vídeo na fila e para a restauração da alta definição.

### Produção em massa (para um perfil no Instagram, por exemplo)

Para fazer muitos vídeos com a mesma pessoa sem um clique por vídeo:

1. Ponha a imagem da pessoa em `producao/pessoa` (uma só): em pé, como os vídeos, e com o
   mesmo enquadramento deles.
2. Ponha os vídeos de origem em `producao/videos`, quantos quiser. Cada um deve começar com a
   pessoa de frente e com algum cenário à vista.
3. Dois cliques em **Produzir em massa**.

O script liga a GPU se ela estiver desligada, espera a troca de pessoa ficar instalada, envia
os vídeos (antes, encurta cada um para o trecho que será usado, e o envio fica pequeno),
acompanha a fila, baixa cada resultado para `producao/prontos`, converte para o formato do
Instagram (1080 de largura, em pé, quadrado ou deitado conforme o vídeo; 30 quadros por
segundo) e, no fim, desliga a GPU. Cada vídeo de origem, depois de pronto, vai para
`producao/usados`; o que der erro fica onde está, com o motivo em `producao/erros.txt`. Dois
vídeos rodam ao mesmo tempo (um por GPU). A janela pode ser fechada e aberta de novo:
continua de onde parou. O computador precisa estar ligado para os resultados serem baixados.

As opções ficam em `producao/opcoes.txt`: qualidade, quantos segundos de cada vídeo usar e a
partir de onde (`comecar_em`, para pular um começo em que a pessoa está de costas),
alta definição (desligada de fábrica: dobra o tempo), movimento fluido (ligado), formato do
Instagram e se a GPU deve ser desligada no fim.

Quanto rende, pelas medidas de 12/10/2026 (vídeos de 10 s; contas, não medidas de uma fila
grande): na *Rápida*, uns 9 por hora e 250 por semana de cota; na *Boa*, uns 4 por hora e 115
por semana. Três Reels por dia são 21 por semana: cabem com folga na *Boa*.

Os vídeos da produção ficam numa fila que o site não enxerga (senão ele os levaria para a
galeria do navegador); eles aparecem só em `producao/prontos`.

### O que carrega e quando

**Na sessão de costume (só a troca de pessoa)** são 27 GB. O site conecta assim que o ComfyUI
e o codificador de texto (7 GB) estão no lugar; o Wan 2.2 Animate (GGUF Q4), com detecção,
recorte, interpolação e restauração (20 GB), baixa em seguida, e o formulário avisa enquanto
ele não chega. O melhorador de prompt não é baixado: a troca de pessoa não o usa, e a segunda
GPU fica só para o vídeo.

**Com `--tudo`** o estúdio abre assim que o **5B** está pronto (~19 GB). O resto baixa em
segundo plano e aparece sozinho na interface; o diálogo da GPU mostra o que está baixando.

| Ordem | Item | Tamanho | Libera |
|---|---|---|---|
| 1 | Wan 2.2 5B + turbo | 19 GB | Texto → Vídeo, Imagem → Vídeo, Imagem |
| 2 | Wan 2.2 Animate (GGUF Q4) + detecção, recorte, interpolação e restauração | 19 GB | Trocar pessoa |
| 3 | Melhorador de prompt | 8 GB | Melhorar com IA (só com 2 GPUs, como no Kaggle) |
| 4 | Wan 2.2 14B imagem → vídeo (GGUF Q4) | 18 GB | Início + Fim e Imagem → Vídeo no 14B |
| 5 | Wan 2.2 14B texto → vídeo (GGUF Q4) | 18 GB | Texto → Vídeo e Imagem no 14B |

Assim que a troca de pessoa é instalada, as duas GPUs a carregam de antemão (uns 5 minutos,
sem ninguém pedir): o que mais demora na primeira troca de uma sessão é ler os modelos do
disco, não gerar. Um pedido feito nesse meio-tempo passa na frente. O **Melhorar com IA** usa
a segunda GPU e só fica disponível quando ela não está ocupada com vídeo: uns 5 minutos depois
desse carregamento e de cada troca de pessoa.

Cada item só é baixado se couber. No Kaggle o disco diz ter 1 TB livre, mas a sessão morre
quando o que foi gravado passa de uns 90 a 100 GB (aconteceu num teste com um modelo a mais);
por isso o instalador tem um teto próprio de 88 GB, e os cinco itens somam 83. Nada disso
persiste entre sessões: a cada vez os modelos são baixados de novo.

## Colocar o site na internet (opcional)

O atalho local já é um endereço fixo neste computador. Para abrir do celular ou de outro PC,
hospede `site/index.html` em qualquer serviço gratuito de site estático. O arquivo não contém
nenhum segredo.

1. Acesse https://app.netlify.com/drop e arraste a pasta `site`. Crie a conta gratuita quando
   pedir, para o site não expirar. (Cloudflare Pages e GitHub Pages também servem.)
2. Nesta pasta, rode `python free\build.py --site https://SEU-ENDERECO` e reenvie
   `free/wan_studio_free.ipynb` para o Kaggle. O notebook passa a mostrar um link pronto que já
   conecta o site.

A galeria pertence a cada navegador e endereço: o que você gerou pelo atalho local não aparece
no endereço hospedado, nem em outro aparelho. Baixe o que quiser levar.

## Limites do gratuito

- **Cota**: o Kaggle oferece cerca de 30 horas de GPU por semana, em sessões de até 12 horas.
- **Velocidade**: a T4 é uma GPU modesta, de 15 a 20 vezes mais lenta que as placas dos
  serviços pagos. Comece com 5B, Turbo e 480p: uns 2 minutos por vídeo de 3 s. O 14B leva uns
  7. A troca de pessoa leva meio minuto de GPU por segundo de vídeo na qualidade *Rápida*
  (10 s em 5 minutos, 60 s em 25) e o triplo na *Boa*. O primeiro vídeo de cada modelo na
  sessão demora mais, porque carrega os modelos.
  Medido em 13/10/2026 num vídeo de uso real (23,5 s, em pé, *Máxima* com alta definição e
  movimento fluido): 154 s por passo, 12 minutos por pedaço de 49 quadros, 9 pedaços. Foram
  1 h 41 de geração numa GPU só, com a outra parada, e mais 23 minutos de restauração nas
  duas (5 a 6 minutos por pedaço): umas 2 h 10 ao todo.
- **Galeria no navegador**: limpar os dados do navegador apaga a galeria. Baixe o que importa.
- **Regras do Kaggle**: é um serviço de ciência de dados; usar a GPU para gerar vídeo é por sua
  conta. Use com moderação e não gere conteúdo adulto: há relatos de contas bloqueadas por isso.
- **Lightning AI**: os créditos gratuitos são poucos e o tempo ligado os gasta. Quantos a
  conta recebe e quando são renovados, o atalho mostra (as páginas do próprio Lightning se
  contradizem: 15 por mês numa, 5 no cadastro e mais 25 com cartão em outra).
- **Colab**: o notebook também roda lá (GPU T4), mas o plano gratuito pode restringir interfaces
  de IA generativa e tem menos memória; não há segunda GPU para o melhorador de prompt.

## Privacidade e segurança

- Cada sessão ganha um endereço novo e uma senha aleatória. Quem tiver o **código de conexão**
  usa a sua GPU enquanto ela estiver ligada: não compartilhe.
- O notebook carrega a chave de pareamento (`free/pairing.json`). **Mantenha o notebook
  privado** e não publique esse arquivo. O Ligar GPU sempre o envia como privado.
- `free/kaggle_token.txt` é a credencial da sua conta do Kaggle, `lightning/lightning_chave.txt`
  a do Lightning AI, e `free/abrir.html` contém o pareamento: não envie as pastas `free` e
  `lightning` a ninguém nem as publique. A chave do Lightning nunca é enviada para a máquina:
  lá o Wan Studio usa a que o próprio Lightning deixa disponível no Studio. A pasta `site` pode ser
  publicada, não tem segredo nenhum.
- `~/.modal.toml` (na sua pasta de usuário) é a autorização da conta do Modal: quem tiver esse
  arquivo liga máquinas na sua conta e gasta o seu crédito. O endereço e a chave com que o
  site liga a GPU rápida ficam guardados no navegador (e passam por `free/abrir.html`); essa
  chave só serve para ligar a sessão com a configuração que o atalho deixou, não dá acesso à
  conta. Rodar o atalho de novo não a troca; para invalidá-la, apague a entrada `wake` do
  dicionário `wan-studio-estado` no painel do Modal.
- O anúncio da sessão passa pelo ntfy.sh (serviço público e gratuito) cifrado com AES-256-GCM;
  o serviço só vê bytes ilegíveis. Se ele estiver fora do ar, o código de conexão continua
  funcionando.
- Os vídeos saem da GPU direto para o seu navegador e são apagados de lá em seguida.

## Se algo der errado

| Sintoma | O que fazer |
|---|---|
| "Sem internet" (no Ligar GPU ou na célula 1) | Verifique o telefone em https://www.kaggle.com/settings e ligue de novo |
| Célula 1: "nvidia-smi não encontrado" | Telefone não verificado, ou *Session options → Accelerator → GPU T4 x2* |
| Ligar GPU fica "na fila" por muito tempo | É a fila do Kaggle; pode fechar a janela, o site conecta quando liberar |
| O site não conecta | Rode `python free\ligar.py status`; se estiver desligada, ligue de novo |
| "faltou memória na GPU" | Na troca de pessoa, use a qualidade **Rápida**; nos outros modos, 480p, 3 segundos ou o modelo 5B |
| O site só mostra a troca de pessoa | É a sessão de costume. Para os outros modos: `python free\build.py --tudo` e ligue a GPU de novo |
| Ligar GPU rápida: "Payment method required" ou parecido | Cadastre o cartão e confira o limite em https://modal.com/settings/usage |
| Ligar GPU rápida fica em "pedindo a máquina" | A placa está disputada no Modal: espere, ou ligue outra com `python nuvem\ligar.py --gpu L40S` |
| Ligar GPU rápida: "A sessão parou ao ligar" | Rode `python nuvem\ligar.py registro` e me envie o que aparecer |
| O botão **Ligar a GPU rápida** não aparece no site | Ele vem do atalho: dê dois cliques em **Ligar GPU rápida** uma vez neste computador (ou abra neste aparelho o link que ele abriu) |
| Ligar GPU (Lightning): "não aceitou ligar a máquina" | Veja o saldo com `python lightning\ligar.py status` e o telefone da conta; ou escolha outra placa |
| Ligar GPU (Lightning): o site não conecta | Rode `python lightning\ligar.py registro` e me envie o que aparecer |
| A qualidade **Ultra** não aparece | Ela pede uma placa de 24 GB ou mais: ligue pelo **Ligar GPU (Lightning)** |
| Vídeo borrado ou estranho com Turbo | Desmarque **Turbo** (20 passos, mais lento) |
| Modo "Início + Fim" ou "Trocar pessoa" travado | O modelo ainda está baixando, ou faltou disco na sessão |
| Troca saiu na pessoa errada ou sem trocar | A pessoa precisa aparecer no meio do trecho: ajuste **Começar em** |
| Sobrou um pedaço da pessoa original (ponta de cabelo, manga) | Use um vídeo com mais folga em volta dela |
| Gestos rápidos borrados ou com rastro na troca | Use a qualidade **Boa** ou **Máxima**, que geram 12 quadros por segundo em vez de 8 |
| A roupa ou o cabelo mudam ao longo da troca | Use uma imagem com enquadramento parecido com o do vídeo; trechos menores variam menos |
| "não consegui ler o vídeo" | Converta o vídeo para MP4 ou envie um trecho menor |
| Qualquer outro erro | Copie a mensagem do card ou da célula que falhou e me envie |

## O que foi verificado

### Na GPU gratuita do Kaggle (11/10/2026, 2× Tesla T4)

O **Ligar GPU** enviou o notebook, que instalou tudo e rodou de ponta a ponta, em nove sessões
ao longo do dia: o estúdio abriu em uns 4 minutos (5B), a troca de pessoa ficou disponível em
7 e todos os modelos em 11 a 13. Cada modo foi executado pela mesma API que o site usa
(`tests/gpu_real.py`), e os quadros de cada resultado foram conferidos um a um:

| Teste (Turbo, salvo indicação) | Tamanho | Tempo |
|---|---|---|
| Texto → vídeo, 5B, 3 s, primeiro vídeo da sessão | 832×480 | 6,5 min |
| Texto → vídeo, 5B, 3 s, modelos já carregados | 832×480 | 1 min |
| Imagem → vídeo, 5B, 3 s | 640×640 | 2 min |
| Texto → vídeo, 5B, 3 s, 720p | 1280×704 | 3,5 min |
| Texto → vídeo, 5B sem Turbo, 3 s | 832×480 | 5 min |
| Texto → vídeo, 14B, 3 s | 832×480 | 7 min |
| Imagem → vídeo, 14B, 3 s | 640×640 | 7 min |
| Início + fim, 14B, 3 s | 832×480 | 7 min |
| Imagem, 5B (20 passos) | 1280×704 | 1,5 min |
| Imagem, 14B (20 passos) | 1280×720 | 4,5 min |
| Trocar pessoa, 10 s, Rápida, primeira troca da sessão | 640×640 | 4,9 min |
| Trocar pessoa, 10 s, Rápida, vídeo em pé | 480×832 | 4,5 min |
| Trocar pessoa, 30 s, Rápida | 640×640 | 13,4 min |
| Trocar pessoa, 10 s, Boa | 640×640 | 13,3 min |
| Melhorar prompt | | 4 a 8 s |

Os modelos de 14B e o Animate cabem na memória da T4 (o registro do ComfyUI mostra carga
completa, ou 0,2 GB de fora no Animate). O tempo é da placa, não de configuração.

Também funcionaram de verdade: o site conectando sozinho à sessão, o áudio do vídeo original
na troca de pessoa, as duas GPUs gerando pedaços ao mesmo tempo, o carregamento antecipado da
troca de pessoa (5,5 minutos nas duas GPUs, logo depois de instalada), o pedido de
desligamento que o botão **Desligar GPU** faz (sessão encerrada em 30 s) e o desligamento
automático após 60 minutos sem uso.

**Como a troca de pessoa ficou mais rápida.** De manhã, 5,8 s de vídeo levaram 24 minutos e
10 s em 720p, mais de 3 horas. Cada mudança abaixo foi medida na T4 antes de entrar:

| Mudança | Medido |
|---|---|
| 4 passos em vez de 6 | mesmo resultado lado a lado, com um terço a menos de passos |
| Pedaços de 49 quadros nas duas GPUs, do meio do vídeo para as pontas | as duas trabalham o tempo todo, sem uma atrasar a outra |
| Detecção de pose na GPU (onnxruntime-gpu 1.22) | de 93 s para 10 s por pedaço |
| Gerar 8 quadros por segundo e interpolar os demais (RIFE) | 10 s de vídeo: de 7,6 para 4,6 min |
| Carregar os modelos antes do primeiro pedido | primeira troca da sessão: de 10,6 para 4,9 min |
| Esticar o último pedaço em vez de gerar um pedaço mínimo | 10 s na Boa: de 18 para 13,3 min |

Testado e deixado de fora: `--use-sage-attention` (não compila na T4 e cai no padrão),
`--fast fp16_accumulation` (quebra o Animate em GGUF), 3 passos (resultado mais mole),
pedaços independentes um do outro (a aparência muda de um para o outro) e as versões novas do
onnxruntime-gpu (pedem CUDA 13 e caem para a CPU sem avisar).

O que só o teste real mostrou, e o que foi feito:

- **A troca de pessoa devolvia o vídeo enviado em vez do gerado.** O ComfyUI lista no
  histórico, antes do resultado, uma prévia do vídeo de entrada, e o servidor pegava o
  primeiro arquivo. Corrigido; o ComfyUI simulado dos testes passou a imitar isso.
- **Na troca, só a cabeça (ou só o tronco) era trocada.** O recorte partia de uma caixa no
  primeiro quadro. Agora o SAM2 recebe, em vários quadros de cada pedaço, a caixa em volta de
  todos os pontos da pose (`vm/nodes/wan_studio`); as quatro estratégias testadas foram
  comparadas pintando o recorte de vermelho sobre o vídeo, e só essa cobriu a pessoa inteira
  do começo ao fim.
- **Emenda entre pedaços**: um pedaço saía com saturação cerca de 10% maior que o anterior. A
  emenda agora mede a cor dos dois lados e iguala (o vídeo original serve de controle para
  não corrigir o que é mudança de cena).
- **Imagem com Turbo saía ruim** (borrada no 5B, com trama no 14B). Imagens agora usam sempre
  os 20 passos.
- **O preset "Grua subindo" desenhou um guindaste**, porque o texto dizia "Crane up". Os
  textos de câmera foram reescritos sem nomes de técnica que também são nomes de coisas.
- **Turbo do 5B**: bom para rascunho, com algum rastro em movimentos rápidos; o 14B é
  visivelmente melhor e mais lento.
- **Fila do Kaggle**: de manhã a sessão esperou 35 minutos antes de ligar; à noite ligou na
  hora nas três vezes. O Ligar GPU e o site tratam essa espera (e uma queda de internet no
  meio dela) como normal.

Limites que os testes mostraram na troca de pessoa e que continuam:

- **Cabelo e chapéu variam ao longo de vídeos longos.** A roupa se mantém, mas o gorro do
  personagem de teste sumiu em alguns pedaços e a trança da pessoa original reapareceu: o
  modelo segue a silhueta recortada, e cada pedaço só vê 5 quadros do vizinho. Acontece mais
  quando a imagem e o vídeo têm enquadramentos muito diferentes (o teste usou um boneco de
  corpo inteiro num vídeo de busto).
- **A Rápida é mais mole** que a Boa, e em enquadramento muito fechado o rosto perde detalhe.
- **Quadros interpolados**: gestos muito rápidos podem borrar por um quadro.
- **O rosto é o do vídeo quando a imagem não tem rosto humano** (o boneco de teste tem uma
  câmera no lugar da cabeça; o resultado ganhou o rosto da pessoa original).

### Qualidade da troca de pessoa (12/10/2026)

O pedido era chegar mais perto de um serviço pago. No mesmo trecho de 3 s (qualidade *Boa*),
uma coisa mudada por vez:

| O que foi testado | Resultado | Decisão |
|---|---|---|
| Restauração de vídeo (SeedVR2) até 720p | olhos, cabelo e tecido mais definidos; 2 min a mais por pedaço | virou a opção **Alta definição** |
| Gerar com 44% mais pixels (768×768) | os detalhes da imagem da pessoa saem como nela (um braço mecânico que em 480p virava rabisco); 1,75 vez o tempo | virou a qualidade **Máxima** |
| Modelo menos comprimido (Q6 em vez de Q4) | diferença mínima, no mesmo tempo, com 3,7 GB a mais | não adotado |
| Imagem de uma pessoa realista, com enquadramento parecido com o do vídeo | rosto, cabelo e roupa iguais do começo ao fim de 10 s | recomendação de uso |

A troca de 10 s com pessoa realista, *Boa* e alta definição saiu em 720×720 a 24 fps, em 23
minutos (18 com as GPUs já aquecidas, pela conta das fases). Sobrou do vídeo original o que
tem silhueta muito diferente da pessoa nova: a manga larga virou um pano e a trança reapareceu
no fim. Ficou também um contorno fino em volta da pessoa em alguns quadros.

Três defeitos apareceram só na GPU, e foram corrigidos:

- **A sessão morreu com o disco em modo somente leitura** ao baixar um modelo a mais para o
  teste: o Kaggle tem um teto de gravação que o disco não informa. O instalador ganhou um teto
  próprio (`DISK_BUDGET_GB`), e os modelos 14B passaram para uma versão 0,8 GB menor cada.
- **Um ComfyUI era morto pelo sistema** (SIGKILL, falta de RAM) quando a restauração rodava
  junto com a geração, e depois quando as duas GPUs soltavam os modelos ao mesmo tempo. A
  restauração virou uma fase separada, depois de todos os pedaços, e a memória é liberada em
  uma GPU por vez. O launcher passou a registrar com que sinal um processo morreu.
- **Uma GPU parada segurava a outra**: as consultas de fila ganharam tempo limite.

### Por que a troca de pessoa tinha piorado (12/10/2026)

O usuário avisou que os vídeos estavam saindo "bugados", piores que os das primeiras versões.
Comparando os exemplos guardados, quadro a quadro, era verdade: o de 3 s da primeira versão é
fiel à imagem da pessoa; os de 10 s das versões "rápidas" têm cor estourada, gorro que vira
cabelo e roupa que muda. Os mesmos 10 s do exemplo oficial foram gerados de novo na GPU,
mudando uma coisa por vez:

| O que foi gerado | Resultado | Tempo |
|---|---|---|
| Como a primeira versão: 16 fps, 6 passos, pedaços de 77, só avançando | fiel do começo ao fim | 53,6 min (uma GPU) |
| O mesmo com 4 passos | igual ao de 6 passos | 26,6 min (uma GPU) |
| O mesmo com 4 passos, gerado menor (a *Rápida* de hoje) | coerente, com menos detalhe | 11,6 min |
| A *Boa* de antes (12 fps e interpolação, pedaços de 49), só avançando | ruim desde o primeiro quadro: cabelo da pessoa original no lugar do gorro | 19,5 min (uma GPU) |
| 16 fps, começando por um pedaço curto no meio (duas GPUs) | objeto inventado na cabeça, herdado por todos os pedaços | 23 min |
| O mesmo, com o pedaço do meio inteiro (77 quadros) | sem o objeto, mas com um lenço inventado no começo | 22,9 min |
| Recorte da pessoa em retângulo (o padrão do código oficial) | pior: óculos e objetos inventados | 13 a 15 min |

O que voltou: todos os quadros gerados, pedaços de 77, sempre do começo para o fim. O que
ficou das versões rápidas: os 4 passos. O que ainda aparece em todos os testes, inclusive no
melhor: a trança da pessoa original volta nos últimos segundos, porque o recorte (SAM2) não a
pega quando ela fica de lado. Os vídeos desses testes estão em `resultados/`.

### A roupa da pessoa original ficava no vídeo em pé (12/10/2026)

A produção em massa foi rodada de ponta a ponta no Kaggle (ligou a GPU, enviou, acompanhou,
baixou, converteu para 1080×1920 a 30 quadros por segundo com o som e arquivou o vídeo de
origem), e o primeiro vídeo em pé saiu errado: rosto e cabelo da pessoa nova, roupa da
original. Com uma foto de pessoa de meio corpo no lugar do personagem do exemplo, o mesmo. A
alta definição e o tamanho dos pedaços não tinham culpa (sem eles, igual).

A prévia do recorte no tamanho real da geração mostrou o motivo. No primeiro quadro, com a
pessoa de costas enchendo o quadro, o SAM2 deixava de fora o miolo das costas e a barra da
jaqueta. O modelo via esse pedaço de roupa, completava a roupa em volta dele e a mantinha no
vídeo inteiro, mesmo nos quadros em que o recorte estava certo. É o mesmo mecanismo da trança
que volta no fim do exemplo oficial.

| Recorte | Primeiro quadro | Vídeo gerado (foto de pessoa, 5 s em pé) |
|---|---|---|
| Como era (caixa em volta da pose + pontos do corpo) | miolo e barra da jaqueta de fora | jaqueta da pessoa original no vídeo inteiro |
| Caixa mais larga | igual | não gerado |
| Só os pontos do corpo (o método do código oficial) | miolo coberto, barra de fora | não gerado |
| Os dois juntos + fechamento (o padrão de hoje) | pessoa coberta inteira | pessoa trocada por inteiro, do primeiro ao último quadro |

O padrão de hoje: o SAM2 recebe as duas pistas como dois objetos e a máscara é a união; se a
pessoa está cortada pela borda de baixo do quadro (a pose não acha os tornozelos na maior
parte do pedaço), o recorte desce até a borda onde já chega perto dela e o que fica cercado é
tampado; e cada quadro leva também o recorte dos dois vizinhos. Em vídeo de corpo inteiro o
fechamento não age. No exemplo quadrado o recorte ficou igual ao de antes e o vídeo gerado
também (a trança ainda volta no fim). Uma caixa com folga em volta da cabeça foi testada e
tirada: num close cobria o quadro quase todo.

Ficou um limite: quando o trecho começa com a pessoa de costas enchendo o quadro (99% dele
dentro do recorte), o modelo não tem cenário em que se apoiar e inventou uma mesa na borda de
baixo. O mesmo vídeo a partir de 1,5 s, com a pessoa de frente, saiu limpo.

Outras medidas do dia:

- **Gerar 15 quadros por segundo** (para o arquivo sair com 30 exatos, o que o Instagram
  mostra): pior. O gorro do personagem sumiu desde o primeiro quadro e a manga da pessoa
  original ficou, como no teste antigo a 12 por segundo. O modelo quer os 16 dele.
- **Movimento fluido** (16 → 32 por interpolação, depois da geração): os quadros calculados
  saíram limpos, sem fantasma, com e sem alta definição.
- **Alta definição em vídeo em pé**, com pedaços de 49 quadros: funciona, a emenda entre os
  pedaços não aparece, e a restauração leva mais que a geração (15 min contra 12, em 5 s).
- **Imagem quadrada em vídeo em pé**, com a foto de uma pessoa (rosto e meio corpo): a pessoa
  saiu igual à do teste com a imagem em pé. O teste antigo, em que o gorro se perdia, era com
  o personagem de corpo inteiro do exemplo, que fica pequeno entre as faixas pretas.
- **O site aberto levava os resultados dos testes** para a galeria do navegador; os testes de
  `tests/gpu_real.py` passaram a usar a fila que o site não enxerga, como a produção.

### Em teste, ainda sem conferência na GPU (13/10/2026)

O que está no código, desligado por padrão, porque este projeto só adota o que foi medido na
GPU (veja *Por que a troca de pessoa tinha piorado*). Nada disto foi gerado ainda: a cota da
semana no Kaggle acabou no dia em que foi escrito, e a GPU rápida ainda não tinha conta.

| Ajuste | O que faz | Como testar | O que olhar |
|---|---|---|---|
| `chains=2` | **Duas cadeias de pedaços, uma em cada GPU do Kaggle.** Hoje os pedaços saem um depois do outro numa GPU e a outra fica parada. A primeira cadeia começa no primeiro quadro; a segunda começa perto do meio e, para herdar a aparência, recebe quadros já gerados do primeiro pedaço (a *âncora*). Na emenda entre as duas, os quadros que ambas geraram se fundem. Pelo plano, o vídeo de 23 s cai a 57% do tempo | `python tests\gpu_real.py juntos c_base c_duas c_livre` | o tempo; e os quadros em volta da emenda (`python tests\gpu_real.py quadros c_duas 92 106`): se a pessoa muda de um quadro para o outro, e se a segunda cadeia inventou algo, como fazia o pedaço do meio de 12/10 |
| `chains=2`, `anchor=false` | O mesmo, com a segunda cadeia partindo só da imagem da pessoa: as duas GPUs começam juntas e o tempo cai a perto da metade, mas nada liga a aparência de uma cadeia à da outra | o `c_livre` acima | o mesmo, com mais atenção a cabelo e roupa de um lado e do outro da emenda |
| `ref=crop` | Recorta a imagem da pessoa na caixa dela (pose + detector), na proporção do vídeo, antes de ela entrar no Animate. Hoje a imagem inteira entra com faixas pretas, e a pessoa fica pequena quando a proporção difere | `python tests\gpu_real.py juntos troca_vertical troca_vertical_crop` | o gorro e a roupa do personagem de corpo inteiro, que se perdiam com a imagem quadrada num vídeo em pé |
| `box=larga`, `grow=16` | Caixa mais folgada em volta da pose e contorno mais engordado no recorte do SAM2 | `python tests\gpu_real.py juntos r_larga_rapida r_base_rapida` | se a trança da pessoa original ainda volta nos últimos segundos, e se o modelo inventou algo |
| `unet=int8` | **O Animate em INT8** (o arquivo oficial da Comfy-Org, 17 GB) em vez do GGUF Q4. O ComfyUI fixado aqui (0.39) tem núcleos próprios para INT8, que só ligam com o PyTorch de CUDA 13; num relato com uma T4 e outro modelo, o INT8 rendeu 1,3 vez o fp8. Pela tabela de quem publicou o formato, a qualidade fica acima da do fp8 (e portanto do Q4) | gere o notebook com `python free\build.py --env TORCH_INDEX=cu130 --env ANIMATE_INT8=1`, ligue, e `python tests\gpu_real.py juntos i_q4 i_int8` | no registro (`log comfyui` e `log comfyui1`): o driver da placa (o CUDA 13 pede o 580 ou mais novo; com um mais antigo o instalador avisa e mantém o PyTorch da imagem, e aí o INT8 não rende), os segundos por passo de cada um e quanto do modelo ficou fora da placa (17 GB não cabem inteiros numa T4); e os quadros lado a lado |
| `whole=true` | **A pessoa inteira.** O recorte do SAM2 é completado pelo de um modelo de recorte de primeiro plano (BiRefNet-portrait, licença MIT), só com os pedaços que saem da cabeça (cabelo, tranças, chapéu) e são pequenos perto da pessoa. No exemplo de 10 s da pasta `exemplos`, do meio para o fim, as tranças da pessoa original voltam grudadas na pessoa nova; rodado aqui nos quadros desse exemplo, o BiRefNet as pega em todos. Em fotos com várias pessoas ele junta quem está encostado e o que a pessoa segura (os bastões de um esquiador): por isso só entra o que sai da cabeça | gere o notebook com `python free\build.py --env PESSOA_INTEIRA=1`, ligue, e `python tests\gpu_real.py juntos w_base w_inteira` | se as tranças somem do meio para o fim (`quadros w_inteira 90 148`) e, no registro, quanto o recorte cresceu (`[wan_studio] pessoa inteira: ...`) |
| `face=true` | **O rosto em detalhe.** Depois de gerar cada pedaço, uma segunda passada do Animate só na cabeça, ampliada até 3 vezes, com a cabeça da foto da pessoa como referência; o resultado volta para o lugar. Num plano aberto o rosto gerado em 480p tem de 60 a 90 px, e é aí que a semelhança com a foto se perde. Custa de 25 a 55% a mais de tempo quando roda; num close, com o rosto já grande, ela não roda | `python tests\gpu_real.py juntos f_base f_rosto` | se o rosto do `f_rosto` se parece mais com a foto, se a cabeça casa com o pescoço e o cabelo em volta, e o tempo de cada um |
| `window=true` | **A janela na pessoa.** O Animate gera só o retângulo do quadro em que a pessoa está (com cenário em volta) e os quadros gerados voltam para o vídeo só onde o recorte dela manda; o resto do quadro fica com os pixels do vídeo de origem, em vez de passar pelo modelo. O passo custa mais que proporcionalmente aos pixels (na T4, 1,5 vez os pixels levou 1,9 vez o tempo): com a pessoa em metade do quadro, o pedaço sairia em bem menos da metade do tempo. Quando ela enche o quadro (mais de 85% dele), nada muda. A cor de cada pedaço é igualada à do anterior dentro do recorte | `python tests\gpu_real.py juntos j_base j_janela`, e `bateria j_vertical` para um vídeo em pé | no registro, o tamanho da janela (`[wan_studio] janela: ...`) e os segundos por passo; nos quadros, se dá para ver a borda da colagem em volta da pessoa, se ela saiu igual à do `j_base` e se a emenda entre os pedaços aparece (`python tests\gpu_real.py quadros j_janela 70 84`) |

Na GPU rápida os mesmos testes rodam com `WAN_BACKEND=nuvem` na frente (no PowerShell:
`$env:WAN_BACKEND = "nuvem"`); lá há uma placa só, e as duas cadeias saem uma depois da outra:
serve para ver a emenda, não o ganho de tempo.

O `ref=crop` sem pessoa achada na imagem (pose e detector falham) devolve a imagem como veio, e
com a pessoa já ocupando quase toda a imagem não recorta. Para adotar qualquer um, mude o padrão
em `app/workflows.py` (`SWAP_CHAINS`, `SWAP_WINDOW`, `SWAP_WHOLE`, `SWAP_FACE`, `REF_FITS`, `MASK_BOXES`, `MASK_GROW`), refaça
`tests/padrao_troca.json` (o resumo do grafo padrão, que os testes conferem) e rode
`python free\build.py`.

Fora da tabela, por ser só um número: **3 passos em vez de 4** (`python tests\gpu_real.py juntos
i_q4 p_3passos`). A LoRA foi destilada para 4; com 3 o pedaço levaria um quarto a menos, se a
pessoa não perder detalhe.

### No ComfyUI de verdade, sem GPU (13/10/2026)

Com a cota de GPU da semana esgotada, o código novo rodou numa sessão **de CPU** do Kaggle,
que não gasta cota (`python free\build.py --cpu`, `python free\ligar.py --cpu` e, com
`WAN_BACKEND=cpu`, os testes `x_` de `tests/gpu_real.py`): o ComfyUI e os nós de verdade, os
modelos de verdade, em 128 px e um passo só. Um pedaço de 17 quadros leva uns 20 minutos (o
recorte da pessoa, 6; o passo, 8), e o resultado é um borrão: isso prova que os grafos rodam
do começo ao fim, não que o vídeo presta nem quanto tempo leva.

| Teste | O que exercita | Resultado |
|---|---|---|
| `x_base` | o pedaço de sempre: pose, recorte pelo SAM2, Animate em GGUF Q4 com as duas LoRAs, emenda | rodou: 17 quadros em 19,6 min, com a cena e uma pessoa no lugar da outra |
| `x_crop` | `ref=crop` (a imagem da pessoa recortada na caixa dela), num vídeo em pé | rodou: 10,7 min, com os modelos já carregados |
| `x_int8` | o Animate em INT8 com as LoRAs | pela metade: o ComfyUI reconheceu o arquivo ("Found quantization metadata", com as operações de INT8 entre as nativas), carregou os 19,7 GB e começou o passo; depois de 35 minutos nele, sem terminar, foi interrompido para dar a vez aos outros testes. Na CPU cada camada é desfeita, somada à LoRA e refeita; o que isso custa e rende numa placa só a GPU diz |
| `x_janela` | a janela na pessoa, num quadro deitado | rodou (30 min): os nós da janela e da colagem rodaram no ComfyUI de verdade e a pessoa nova saiu no meio do cenário de origem. Num quadro tão pequeno (384×224) a pessoa ocupa quase tudo, e a janela ficou do tamanho do quadro, como deve: o corte em si só se confere em quadros maiores, na GPU |
| `x_recorte` e `x_inteira` | só o recorte (pintado de vermelho, sem gerar) em 1,8 s do exemplo em que as tranças voltavam: o de sempre e o da pessoa inteira | rodaram (15 e 43 min). O de sempre deixou de fora a trança solta e o enfeite da cabeça em 13 dos 29 quadros; a pessoa inteira os pegou em todos, sem tirar nada do recorte (a imagem está em `exemplos`: "recorte da pessoa - o de sempre (esquerda) e a pessoa inteira (direita)"). É a causa do defeito do exemplo de 10 s, corrigida na origem; falta ver na GPU o vídeo gerado com ele |
| `x_janela2` | a janela num quadro mais largo (576×224), em que sobra cenário dos lados | rodou (28 min): a pessoa nova no meio, o cenário de origem dos lados e nenhuma borda à vista. O passo levou menos que o de um quadro menor e inteiro, sinal de que a janela cortou; o tamanho dela, que o registro diria, não ficou guardado (a sessão desligou antes) |
| `x_rosto` | o rosto em detalhe, num quadro deitado | rodou (33 min): a janela da cabeça foi achada e ampliada a 512 px, a segunda passada rodou e a cabeça voltou para o lugar sem borda à vista. Mesmo com um passo só, o rosto sai bem mais definido que sem ela (a imagem está em `exemplos`: "rosto em detalhe - teste na CPU, 1 passo"). Nesse quadro pequeno (384×224) a janela pegou também o tronco; pela conta, com o rosto de 90 px de um quadro de 480p, ela fica na cabeça e nos ombros |
| `x_cadeias` | as duas cadeias, com a âncora e a mistura na emenda entre elas | pela metade: 4 dos 5 pedaços saíram (uns 20 min cada na CPU) e o teste parou no limite de 90 minutos, antes da emenda. A emenda com a mistura é conferida aqui, com o ffmpeg de verdade (*Emenda*, abaixo) |

### Nesta máquina, sem GPU

- **Workflows**: os grafos de 5B e 14B em cada modo passam na validação de um ComfyUI real,
  com as versões fixadas em `vm/setup.sh`; os da troca de pessoa atual foram executados na
  GPU do Kaggle.
- **Downloads**: as 26 URLs de modelo existem e têm o tamanho esperado.
- **Emenda**: 24 verificações com o ffmpeg de verdade: plano de pedaços para qualquer
  duração, cada quadro vindo do pedaço certo (com e sem quadros interpolados), cor igualada,
  corte de cena; e as duas cadeias: o plano para qualquer duração, a mistura na emenda entre
  elas e a cor de uma trazida à da outra.
- **Motor**: 46 verificações pela API com duas GPUs simuladas: pedaços em fila do começo ao
  fim (o padrão) e, por ajuste de teste, nas duas GPUs a partir do meio, em duas cadeias
  (com e sem âncora), com a janela na pessoa, a pessoa inteira ou o rosto em detalhe; a sessão
  só de troca avisando o site; vídeo longo,
  aquecimento, alta definição em fase separada, cancelamento no meio, falta de memória,
  melhorador cedendo a GPU; e, com uma placa de 45 GB simulada, a qualidade *Ultra* (recusada
  na T4; 720p direto, pedaços de 77 quadros e modelo fp8 na placa grande).
- **Site e servidor**: 95 verificações num Chrome real contra um ComfyUI simulado: parear,
  conectar, gerar, trocar pessoa, fila, cancelar, erro, galeria persistente, Animar, Continuar,
  celular, desligar, e a *Ultra* aparecendo só na placa grande; o botão de ligar a GPU rápida
  (com a chave aceita e recusada), o tempo estimado em cada qualidade, a comparação com o
  vídeo original; e a página só de troca de pessoa: é como o site abre na primeira visita
  (sem abas, com os três passos na galeria vazia), as abas e os cartões dos outros modos
  aparecem numa sessão que os ofereça e somem de novo numa só de troca.
- **Recorte**: 57 verificações da parte dos nós do ComfyUI que é só conta: o fechamento do
  recorte da pessoa (cortada pela borda, bolsões, corpo inteiro intacto, as duas pistas por
  quadro-chave), o recorte da imagem da pessoa na proporção do vídeo (`ref=crop`), a janela
  na pessoa (o tamanho dela em cada tipo de cena, a colagem de volta no vídeo, a cor trazida à
  do pedaço anterior, corte de cena), a pessoa inteira (a trança e o chapéu entram; o bastão
  na mão e o vizinho encostado não) e o rosto em detalhe (a janela da cabeça, a ampliação, a
  segunda passada que nem roda quando o rosto já é grande). O SAM2, a detecção e o BiRefNet
  em si só se conferem na GPU (o BiRefNet foi rodado aqui, na CPU, nos quadros do exemplo e em
  quatro fotos).
- **O grafo padrão não mudou**: sem ajuste de teste nenhum, o grafo de cada pedaço da troca
  de pessoa é, nó por nó, o do código que foi medido na GPU (12 combinações de qualidade, alta
  definição, movimento fluido e pedaço que continua de outro, conferidas contra o resumo em
  `tests/padrao_troca.json`). Tudo o que entrou em 13/10/2026 só age quando pedido.
- **Produção em massa**: 9 verificações contra o servidor de verdade com dois ComfyUI
  simulados: pastas criadas e explicadas, dois vídeos em pé produzidos com um ilegível no
  meio, saída em 1080×1920 a 30 quadros por segundo com o som, vídeos de origem arquivados,
  fila separada da do site, retomada depois de interrompido, desligamento no fim.
- **Ligar GPU (Lightning)**: 45 verificações contra uma API do Lightning AI simulada, com as
  rotas e os campos do cliente oficial (lightning-sdk 2026.10.1): criar o Studio, escolher a
  placa pelo que a conta oferece, ligar, enviar o pacote (os comandos remotos rodam de verdade
  num bash local: o pacote chega inteiro e um pedaço perdido é barrado pela conferência),
  iniciar, status, registro, desligar, chaves coladas de vários jeitos, e a máquina sendo
  desligada no fim, inclusive quando a instalação falha.
- **Ligar GPU rápida (Modal)**: 33 verificações com um `modal` de mentira que imita o que se usa
  do cliente oficial (1.6.1). O atalho autoriza, publica, baixa os modelos e liga; a sessão que
  sobe é a de verdade (`nuvem/sessao.py`, o launcher e o servidor), sobre o ComfyUI simulado:
  responde no túnel com a placa, o preço por hora e a troca de pessoa instalada; liga de novo
  sem duplicar, desliga pela API, desliga sozinha por ociosidade, sobe pelo pedido do site
  (*acordar*), e trata conta sem cartão, download que falha e sessão que para ao ligar. A
  definição do app também foi carregada com o cliente de verdade, que aceita todos os
  parâmetros usados; publicar e rodar ficaram sem conferir (veja abaixo).
- **Ligar GPU**: 20 verificações contra uma API do Kaggle simulada, incluindo conta sem
  telefone verificado, sessão presa na fila e queda de internet logo depois do envio.
- **Descoberta automática**: ida e volta pelo ntfy.sh real, com a cifra de verdade.

### Ainda não verificado

- **Uma fila grande na produção em massa**: no Kaggle ela rodou de ponta a ponta com um
  vídeo. Dois ao mesmo tempo, a retomada e o desligamento no fim só foram conferidos com o
  ComfyUI simulado.
- **O recorte de hoje em vídeo de corpo inteiro, deitado, e com fotos e vídeos do usuário**:
  os testes foram com o exemplo oficial (quadrado, e o miolo dele em pé, de meio corpo).
- **A trança que volta no fim do exemplo oficial**: continua no padrão. Fica à esquerda da pessoa,
  solta, e nenhum dos métodos de recorte do SAM2 a pegou; com a pessoa inteira (em teste) a prévia do
  recorte, na CPU, passou a pegá-la. Falta o vídeo gerado com ela, na GPU.
- **Tudo do Modal numa conta de verdade**: se a imagem monta (o instalador roda sem GPU e com
  o PyTorch de CUDA 13, combinação nunca usada aqui), se o `onnxruntime-gpu` atual acha a GPU
  (senão a detecção de pose cai para a CPU, mais lenta), se os modelos chegam ao volume, quanto
  a sessão leva para subir, se o túnel do Modal aceita o envio de vídeos de 100 MB e, acima de
  tudo, **quanto tempo a troca de pessoa leva em cada placa e quanto custa**. As estimativas do
  site e deste arquivo para a H100 saem de um fator de 9 a 10 vezes sobre a T4.
- **As duas cadeias**, a janela na pessoa, o Animate em INT8, o recorte da imagem da pessoa e
  a caixa larga: veja *Em teste*. No ComfyUI de verdade eles só rodaram em CPU, em 128 px.
- **A sessão só de troca de pessoa numa GPU de verdade**: quanto ela leva para conectar e para
  liberar a troca, agora que não baixa o 5B. Numa sessão de CPU do Kaggle (a mesma rede e os
  mesmos downloads) ela subiu, avisou o site de que só faz a troca e a liberou em 5 minutos.
- **Tudo do Lightning AI numa conta de verdade**: o simulador segue o cliente oficial, mas o
  serviço pode responder diferente. Em especial: se a conta gratuita pode ligar a L40S, quanto
  disco ela guarda, se o comando remoto aceita o tamanho dos pedaços do pacote, se as chaves
  da conta ficam disponíveis dentro do Studio (é com elas que a máquina se desliga sozinha;
  sem elas, quem desliga é o repouso automático do Lightning, uns 10 minutos depois de o Wan
  Studio parar) e o que a instalação encontra na imagem do Studio.
- **A qualidade Ultra numa placa de verdade**: tempo, uso de memória (na L4, de 24 GB, o
  modelo em fp8 e os quadros em 720p podem não caber juntos) e o resultado. A estimativa de
  tempo que o site mostra para ela é uma conta pela ficha técnica das placas.
- Trocas de 60 s (a mais longa gerada e conferida foi de 30 s), a qualidade Máxima em vídeo
  de mais de um pedaço e a alta definição em vídeo deitado ou em pé (os testes foram com vídeo
  quadrado, que sai em 720×720; em 1248×720 a restauração deve levar mais).
- Vídeos com várias pessoas e fotos de pessoas reais na troca: os testes usaram o exemplo
  oficial do ComfyUI, com um personagem.
- Vídeos de 5 s no 14B, 720p no 14B e o lote de 4.
- Os 36 presets de câmera um a um: depois da reescrita, só "Dolly In" e "Grua subindo" foram
  gerados de verdade. Os efeitos e os estilos, só "Cinemático".
- O notebook no Colab e o servidor em máquina própria.

## Para mexer no código

```
app/      servidor (aiohttp), workflows do ComfyUI, presets, melhorador, interface
free/     build.py gera o site e o notebook; ligar.py liga a GPU pela API do Kaggle;
          launcher.py roda dentro do notebook; producao.py faz a produção em massa
producao/ pastas da produção em massa (pessoa, videos, prontos, usados) e as opções dela
lightning/ ligar.py liga a GPU pela API do Lightning AI; boot.py roda dentro da máquina
nuvem/    a GPU rápida: wan_nuvem.py é o app do Modal (imagem, volume, sessão, acordar);
          sessao.py roda dentro da máquina; ligar.py é o atalho
site/     index.html gerado (não edite: altere app/static e rode o build)
vm/       setup.sh instala ComfyUI e modelos; demais scripts são para máquina própria
tests/    testes de ponta a ponta sem GPU, e gpu_real.py para testar na GPU ligada
exemplos/ resultados gerados na GPU gratuita
```

Depois de alterar qualquer coisa em `app/`, `vm/` ou `free/launcher.py`:

```powershell
python free\build.py        # regenera site/index.html e o notebook (sessão só de troca de pessoa)
python free\build.py --tudo # o mesmo, com a sessão que baixa todos os modos
python tests\run_all.py     # opcional: roda os testes (veja os requisitos no próprio arquivo)
```

O **Ligar GPU** envia o notebook novo a cada vez que liga; se a GPU já estiver ligada, desligue
antes. O **Ligar GPU rápida** republica sozinho quando `app/`, `vm/`, `free/launcher.py` ou
`nuvem/` mudam (só o que muda em `vm/setup.sh` refaz a imagem); a sessão que já estiver no ar
segue com o código antigo até ser desligada. O **Ligar GPU (Lightning)** não usa o notebook: empacota e envia o código atual a cada
vez que inicia o Wan Studio na máquina. Se o site estiver hospedado, reenvie também a pasta `site`.

Os testes de `run_all.py` usam um ComfyUI simulado: provam o site e o servidor, não que um
vídeo sai. Para isso, com a GPU ligada (gasta a cota):

```powershell
python tests\gpu_real.py bateria t2v_5b troca   # gera de verdade e extrai quadros para conferir
python tests\gpu_real.py log                    # registro do ComfyUI: tempos, memória, erros
python tests\gpu_real.py desligar               # desliga a sessão (não age com vídeo em andamento)
```

Para saber se um grafo novo ao menos roda no ComfyUI de verdade, sem gastar a cota de GPU, há
a sessão de CPU do Kaggle (lenta: 20 minutos por pedaço de 17 quadros em 128 px):

```powershell
python free\build.py --cpu ; python free\ligar.py --cpu    # notebook e sessão próprios ("Wan Studio CPU")
$env:WAN_BACKEND = "cpu"
python tests\gpu_real.py esperar animate
python tests\gpu_real.py bateria x_base x_crop x_cadeias x_int8
python tests\gpu_real.py desligar
```

### Máquina própria com GPU (opcional)

Com uma máquina Linux e GPU NVIDIA, `vm\deploy.ps1 -Target usuario@IP -Models all` instala
tudo por SSH (14B em fp8, que pede 24 GB de VRAM) e `vm\tunnel.ps1 -Target usuario@IP` abre a
interface em http://localhost:8080. Nesse modo a página é servida pelo próprio servidor e não
há o que conectar.
