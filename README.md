### DublaSync 🎙️

O que é o DublaSync?

O **DublaSync** é uma ferramenta de interface gráfica (GUI) intuitiva desenvolvida para automatizar a sincronização de faixas de áudio dubladas com um arquivo de mídia guia (vídeo ou áudio de referência).

Se você trabalha com edição de vídeo, dublagem ou sincronização de áudios extraídos de diferentes fontes (com diferentes taxas de quadros/FPS), o DublaSync resolve o problema de atraso (delay) e dessincronização progressiva (drift). Ele utiliza processamento de sinal avançado (Correlação Cruzada via SciPy) para encontrar o ponto exato de sincronia e aplica correções de tempo de alta qualidade usando o filtro Rubberband do FFmpeg, preservando o tom (pitch) original do áudio.

#### ✨ Principais Recursos:

* **Análise Inteligente de Sincronia:** Calcula automaticamente o atraso (delay) exato em milissegundos e a diferença de tempo/FPS (speed factor) entre a guia e o áudio dublado.
* **Correção Automática (Time-Stretching):** Corrige a velocidade de arquivos de áudio automaticamente usando o encoder FFmpeg ideal e o filtro `rubberband`.
* **Interface Drag & Drop (Arraste e Solte):** Carregue seus arquivos de forma rápida e visual.
* **Seleção de Faixas:** Suporte a arquivos de mídia com múltiplas faixas de áudio (permite escolher qual idioma/canal usar na análise).
* **Gerenciamento Automático de Dependências:** Verifica e instala o FFmpeg e suas dependências automaticamente (incluindo o Rubberband) caso não estejam no sistema.
* **Geração de Comandos CLI:** Permite visualizar e copiar a linha de comando exata do FFmpeg usada para a conversão.
* **Temas e Idiomas:** Suporte nativo a Tema Claro/Escuro e múltiplos idiomas (Português, Inglês, Espanhol).

#### 🚀 Como usar o DublaSync

A interface foi desenhada para ser o mais simples e direta possível. Siga o passo a passo abaixo para sincronizar seus áudios:

##### Passo 1: Preparação

Abra o **DublaSync**. A tela inicial (aba "Sincronização") exibirá dois painéis de "Arraste e Solte" (Drag & Drop).

* (Nota: Se for a primeira vez executando e o programa não encontrar o FFmpeg no seu computador, ele oferecerá a opção de baixar e configurar tudo automaticamente. Basta aceitar!)

###### Passo 2: Carregar o Arquivo Guia (Referência)

1. Arraste e solte o seu **arquivo guia** (o vídeo original ou o áudio que está com o tempo correto) no painel esquerdo chamado **"Guia"** (🎥).
2. Se o arquivo guia contiver mais de uma faixa de áudio, uma janela se abrirá para que você selecione a faixa desejada para a análise.

###### Passo 3: Carregar o Áudio Dublado

1. Arraste e solte o seu **arquivo de áudio dublado** (aquele que precisa ser sincronizado) no painel direito chamado **"Dublado"** (🎙️).
2. Selecione a faixa correspondente caso haja mais de uma.

###### Passo 4: Analisar a Sincronia

1. Com ambos os arquivos carregados, o botão azul **"Analisar"** ficará habilitado.
2. Clique em **"Analisar"**. O DublaSync irá extrair amostras do áudio e calcular a diferença de tempo (delay) e a discrepância de duração (FPS/Fator de velocidade) entre eles.
3. Ao fim do processo, um relatório será exibido na tela, mostrando a diferença de milissegundos, se a qualidade do *Lip-Sync* (sincronia labial) está boa e a qual proporção de FPS os arquivos pertencem.

###### Passo 5: Corrigir a velocidade do Áudio

1. Se o programa detectar que há necessidade de alterar a velocidade/duração do áudio para que ele "encaixe" no vídeo (correção de FPS), o botão verde "Corrigir Velocidade" aparecerá na tela.
2. Clique em "Corrigir Velocidade" para iniciar o processo de conversão.
3. Aguarde a barra de progresso. O novo arquivo de áudio sincronizado será salvo **na mesma pasta do áudio dublado original**, com o sufixo `_fps-corrigido` no nome do arquivo.


### Análise de Diagnósticos

**1. Diagnóstico de quando está tudo ok (As velocidades batem perfeitamente):**
* Este resultado ocorre quando o programa avalia que a diferença percentual de FPS entre a guia e o áudio é muito pequena (menor ou igual a 0,05%).
* O programa conclui que as velocidades do conteúdo batem perfeitamente e informa que nenhuma correção de FPS (velocidade) é necessária.
* O diagnóstico explica que, caso ainda exista alguma assincronia, ela é apenas de delay estático.
* Para resolver isso, o programa fornece o tempo exato de delay (com sinais de positivo ou negativo) que o usuário deve aplicar, sem precisar alterar a velocidade do áudio inteiro.

**2. Diagnóstico de quando a velocidade não está igual (Conversão de FPS padrão):**
* Este cenário acontece quando os arquivos possuem velocidades diferentes, mas a margem de erro aponta para uma conversão de FPS padrão conhecida (por exemplo, de Áudio Cinema 24 para Vídeo PAL 25).
* O programa detecta qual foi a conversão específica realizada e informa o usuário.
* Em seguida, ele gera o comando de correção avançada aplicando a proporção de tempo exata daquela conversão. 
* O ajuste também utiliza o filtro "rubberband" para manter a qualidade e o tom original da voz.

**3. Diagnóstico de sincronização atípica:**
* Este diagnóstico é exibido quando a diferença percentual de velocidade detectada entre os arquivos não corresponde a uma conversão padrão de FPS.
* O programa alerta o usuário de que essa diferença atípica provavelmente significa que o filme possui cenas a mais ou cortadas (como a diferença entre um "Director's Cut" e a Versão de Cinema) ou que houve uma conversão não padrão.
* Mesmo não sendo um padrão conhecido, o programa oferece a opção de forçar a correção do áudio usando o fator de velocidade exato que foi medido. No entanto, isso não é garantia de que o áudio ficará ajustado. É necessário fazer uma verificação manual no áudio para saber se a correção realmente deu certo ou se trata-se mesmo de uma versão diferente do filme.
* A correção é feita sugerindo um comando com o filtro avançado "rubberband", que altera a duração do áudio mas preserva o tom da voz (pitch).
* Para melhor entendimento de quando uma conversão sai do padrão, a tabela abaixo mostra os valores das conversões de FPS comuns. Quando a diferença detectada foge desses padrões, o diagnóstico de sincronização atípica é acionado:

| Conversão (FPS) | Diferença Aproximada | Fator (Multiplicador) |
|---|---|---|
| **23,976 ↔ 24,000** | 0,1% | 1,001001 / 0,999001 |
| **24,000 ↔ 25,000** | 4,167% | 1,041667 / 0,960000 |
| **23,976 ↔ 25,000** | 4,271% | 1,042709 / 0,959040 |


**Dica Extra:** Se você quiser fazer a conversão por conta própria no terminal ou adaptar para um script, após a análise, você pode clicar no botão **"Mostrar linha de comando"** para ver o comando exato que o DublaSync gerou para o FFmpeg.


🔗 https://www.youtube.com/@TutoriaisOnline/videos
🔗 https://github.com/VicioBR/DublaSync
