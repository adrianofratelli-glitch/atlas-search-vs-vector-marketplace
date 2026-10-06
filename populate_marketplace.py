"""
populate_marketplace.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Seeds the MongoDB Atlas POC with a marketplace catalog dataset
(Mercado Livre / Shopee style). The seed content is in Portuguese,
matching the demo's Brazilian audience.

Designed to exercise every engine:
  • Atlas Search  → autocomplete, fuzzy, facets, highlighting on `nome` and `descricao`
  • Vector Search → semantic search via autoEmbed (voyage-4) on `descricao`
  • Hybrid RRF    → combines lexical and semantic in the app
  • AI Agent      → searches, aggregates, compares, recommends via MongoDB tools

The `descricao` field is the core of the demo: rich natural-language text
(~200 words) with usage context, target audience, and benefits — which is what
lets Vector Search find results where Atlas Search returns nothing.

Collections created:
  • produtos          → main volume — Atlas Search + MQL
  • produtos_vector   → semantic subset — Vector Search autoEmbed
  • avaliacoes        → product reviews — enriches the agent

Usage (prefer scripts/reset_demo.py, which also builds the search indexes):
  DB_NAME=marketplace_test TOTAL_DOCS=20000 VECTOR_SAMPLE=3000 TOTAL_AVAL=20000 python populate_marketplace.py
  ALLOW_DEMO_DB_WRITE=1 DB_NAME=POC python populate_marketplace.py   # demo database

Safety: DROP_FIRST=true (default) drops the three collections. Any database
whose name does not end in `_test` is refused unless ALLOW_DEMO_DB_WRITE=1.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT
from pymongo.errors import BulkWriteError

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

MONGODB_URI           = os.getenv("MONGODB_URI", "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/")
DB_NAME               = os.getenv("DB_NAME", "poc_marketplace")

TOTAL_DOCS_PRODUTOS   = int(os.getenv("TOTAL_DOCS",    20_000_000))
VECTOR_SAMPLE_SIZE    = int(os.getenv("VECTOR_SAMPLE",    500_000))
TOTAL_DOCS_AVALIACOES = int(os.getenv("TOTAL_AVAL",     5_000_000))

BATCH_SIZE            = int(os.getenv("BATCH_SIZE", 10_000))
DROP_BEFORE_POPULATE  = os.getenv("DROP_FIRST", "true").lower() == "true"

def assert_writable_db(db_name: str) -> None:
    """Refuse to write into anything but a `*_test` database without explicit
    consent: the demo database (and databases shared with other PoVs) must
    never be dropped by accident."""
    name = (db_name or "").strip()
    if not name:
        sys.exit("❌ DB_NAME vazio — informe o banco de destino.")
    if name.endswith("_test_test"):
        sys.exit(f"❌ '{name}' tem sufixo _test duplicado — confira DB_NAME.")
    if name.endswith("_test"):
        return
    if os.getenv("ALLOW_DEMO_DB_WRITE") == "1":
        print(f"  ⚠  ALLOW_DEMO_DB_WRITE=1 — escrevendo no banco '{name}'.")
        return
    sys.exit(f"❌ Recusado: '{name}' não termina em _test. Para o banco da demo, "
             f"rode de novo com ALLOW_DEMO_DB_WRITE=1 (e nunca durante uma demo ao vivo).")


COL_PRODUTOS        = "produtos"
COL_PRODUTOS_VECTOR = "produtos_vector"
COL_AVALIACOES      = "avaliacoes"

# ══════════════════════════════════════════════════════════════════════
# PRODUCT CATALOG (Portuguese seed content)
# Structure: category → subcategory → [(brand, model, base_price)]
# ══════════════════════════════════════════════════════════════════════

CATALOGO = {
    "Eletrônicos": {
        "Smartphones": [
            ("Samsung", "Galaxy S24 Ultra",    7499), ("Samsung", "Galaxy A55 5G",       2299),
            ("Apple",   "iPhone 15 Pro Max",   9999), ("Apple",   "iPhone 15",            6499),
            ("Motorola","Edge 50 Pro",          3199), ("Motorola","Moto G85",              1699),
            ("Xiaomi",  "14T Pro",              4299), ("Xiaomi",  "Redmi Note 13 Pro",    1999),
            ("Google",  "Pixel 8 Pro",          5999), ("OnePlus", "12",                   4799),
        ],
        "Notebooks": [
            ("Apple",   "MacBook Air M3",       9999), ("Apple",   "MacBook Pro M3 Pro",  14999),
            ("Dell",    "XPS 15",               8999), ("Dell",    "Inspiron 15",          3499),
            ("Lenovo",  "ThinkPad X1 Carbon",   9499), ("Lenovo",  "IdeaPad 5i",           3299),
            ("ASUS",    "ZenBook 14 OLED",      5799), ("ASUS",    "ROG Zephyrus G16",     9999),
            ("Samsung", "Galaxy Book4 Pro",     7499), ("Acer",    "Swift Go 14",          3799),
        ],
        "Fones de Ouvido": [
            ("Apple",   "AirPods Pro 2ª Geração", 1999), ("Apple", "AirPods Max",         3799),
            ("Sony",    "WH-1000XM5",           1999), ("Sony",    "WF-1000XM5",          1799),
            ("Samsung", "Galaxy Buds3 Pro",     1199), ("JBL",     "Tour One M2",          999),
            ("Bose",    "QuietComfort Ultra",   2399), ("Sennheiser","Momentum 4",         1999),
            ("Anker",   "Soundcore Q45",         399), ("Marshall", "Monitor III",          799),
        ],
        "Smart TVs": [
            ("Samsung", "Neo QLED 8K 65\"",    12999), ("Samsung", "QLED 55\"",            3999),
            ("LG",      "OLED C4 65\"",        10999), ("LG",      "NanoCell 55\"",         2799),
            ("Sony",    "Bravia XR A95L 65\"",  9999), ("Sony",    "X90L 55\"",             3499),
            ("TCL",     "QLED 55\" 4K",         1999), ("Philips",  "Ambilight 65\"",       3999),
        ],
        "Tablets": [
            ("Apple",   "iPad Pro M4 13\"",     9999), ("Apple",   "iPad 10ª Geração",     3299),
            ("Samsung", "Galaxy Tab S9 Ultra",  7999), ("Samsung", "Galaxy Tab A9",         1499),
            ("Lenovo",  "Tab P12 Pro",          3499), ("Xiaomi",  "Pad 6",                1999),
        ],
        "Smartwatches": [
            ("Apple",   "Watch Series 9",       3999), ("Apple",   "Watch Ultra 2",         5999),
            ("Samsung", "Galaxy Watch6 Classic", 1999), ("Garmin",  "Fenix 7X Pro",        5499),
            ("Garmin",  "Forerunner 965",       3999), ("Fitbit",  "Sense 2",               799),
            ("Xiaomi",  "Watch 2 Pro",           899), ("Amazfit",  "GTR 4",                699),
        ],
        "Câmeras": [
            ("Sony",    "Alpha A7 IV",          13999), ("Sony",    "ZV-E10 II",            3499),
            ("Canon",   "EOS R6 Mark II",       11999), ("Canon",   "EOS R50",              3799),
            ("Nikon",   "Z6 III",               11999), ("Fujifilm", "X-T5",                9999),
            ("GoPro",   "Hero 12 Black",         2299), ("DJI",     "Osmo Action 4",        1999),
        ],
    },
    "Esportes & Fitness": {
        "Tênis Esportivos": [
            ("Nike",         "Air Zoom Pegasus 41",      649), ("Nike",         "Air Max 270",             799),
            ("Nike",         "React Infinity Run 4",     799), ("Adidas",        "Ultraboost 23",           899),
            ("Adidas",        "Samba OG",                699), ("New Balance",   "Fresh Foam X 1080",       899),
            ("ASICS",         "Gel-Nimbus 26",           849), ("ASICS",         "Gel-Kayano 30",           999),
            ("Under Armour",  "HOVR Infinite 5",         599), ("Brooks",        "Ghost 16",                699),
            ("Puma",          "Velocity Nitro 3",        549), ("Saucony",       "Endorphin Speed 4",       849),
        ],
        "Roupas Esportivas": [
            ("Nike",         "Dry-Fit Camiseta",         199), ("Nike",         "Pro Tight Legging",        299),
            ("Adidas",        "Techfit Compressão",      249), ("Under Armour",  "HeatGear Regata",         179),
            ("Puma",          "Seamless Legging",        229), ("Olympikus",     "Camiseta Dryline",         129),
            ("Lupo",          "Short de Corrida",        149), ("Asics",         "Camiseta Cool Motion",    179),
        ],
        "Equipamentos de Musculação": [
            ("Romafit",      "Kit Halteres Emborrachados 10kg",   299),
            ("Kikos",        "Barra de Musculação Olímpica 2.2m", 499),
            ("Everlast",     "Kit Anilhas Emborrachadas 20kg",    349),
            ("Domyos",       "Colchonete Fitness 15mm",           149),
            ("Acte",         "Kettlebell 16kg",                   249),
            ("Power Systems","TRX Sistema de Suspensão",          299),
            ("Caloi",        "Banco de Musculação Regulável",     449),
            ("Gold'sGym",    "Corda de Pular Speed",              149),
            ("Speedo",       "Elástico de Resistência Kit 5un",   199),
            ("Domyos",       "Step de Aeróbica Regulável",        229),
        ],
        "Suplementos": [
            ("Whey Premium", "Whey Protein Concentrado 2kg",    199), ("Max Titanium", "Whey 100% Concentrado 2kg", 189),
            ("Integral Medica","Whey Fusion 1.8kg",             229), ("Optimum",      "Gold Standard Whey 2.27kg", 349),
            ("Integral Medica","Creatina Monohydrate 300g",     129), ("Growth Supp.", "Creatina 300g",               99),
            ("BioTech",      "BCAA 8:1:1 300g",                 149), ("Growth Supp.", "Glutamina 300g",             119),
            ("Probiótica",   "Hipercalórico 3kg",               199), ("Max Titanium", "Pré-Treino 3D 300g",        149),
        ],
        "Bikes & Ciclismo": [
            ("Caloi",        "Mountain Bike Elite Carbon 29\"", 8999), ("Oggi",        "Big Wheel 7.4 29\"",       3499),
            ("Specialized",  "Rockhopper Comp 29\"",            4999), ("Trek",        "Marlin 6 Gen 3",           3299),
            ("Scott",        "Scale 965 29\"",                  3999), ("Sense",       "Activ 29\" 2024",          2799),
            ("Caloi",        "Speed 10 700c",                   1999), ("Groove",      "Hybrid PRO 700c",          2299),
        ],
        "Natação & Aquáticos": [
            ("Speedo",       "Óculos de Natação Hydrosity",       199), ("Speedo",    "Faixa de Cabelo Adulto",    49),
            ("Arena",        "Maiô de Competição",                349), ("Zoggs",     "Prancha de Natação",         99),
            ("Speedo",       "Touca de Silicone",                  69), ("Kaedo",     "Nadadeiras de Treino",       199),
        ],
    },
    "Moda & Estilo": {
        "Roupas Masculinas": [
            ("Reserva",      "Camisa Linho Slim",              299), ("Osklen",       "T-Shirt Stone Basic",     199),
            ("Forum",        "Calça Jeans Slim",               399), ("Calvin Klein", "Polo Regular",            349),
            ("Tommy Hilfiger","Camiseta Estampada",            299), ("Lacoste",      "Polo Regular",            499),
            ("Hering",       "Camiseta Básica",                 99), ("Zara",         "Blazer Regular",          599),
        ],
        "Roupas Femininas": [
            ("Farm",         "Vestido Midi Estampado",         599), ("Animale",      "Calça Wide Leg",          699),
            ("Zara",         "Blazer Oversize",                499), ("Renner",       "Vestido Floral",          199),
            ("Farm",         "Blusa de Linho",                 299), ("PatBo",        "Jumpsuit",                799),
            ("Shoulder",     "Vestido de Festa",               899), ("Le Lis Blanc", "Calça Alfaiataria",       499),
        ],
        "Calçados Masculinos": [
            ("Vans",         "Old Skool",                      399), ("Converse",     "Chuck Taylor All Star",   349),
            ("Nike",         "Air Force 1 '07",                699), ("Adidas",        "Stan Smith",             599),
            ("Timberland",   "Bota 6-Inch Premium",            899), ("Skechers",      "Go Walk 7",              399),
            ("Reserva",      "Loafer de Couro",                499), ("Caterpillar",   "Colorado Plus",           699),
        ],
        "Calçados Femininos": [
            ("Arezzo",       "Mule Couro",                     499), ("Schutz",       "Sandália Salto Fino",     599),
            ("Melissa",      "Sandália Mar Feminina",           299), ("Vans",         "Old Skool Feminino",      399),
            ("Grendene",     "Ipanema Bossa Soft",              149), ("Anacapri",     "Sapatilha",               249),
        ],
        "Bolsas & Acessórios": [
            ("Petite Jolie", "Bolsa Bucket",                   399), ("Via Mia",      "Bolsa Tote",              299),
            ("Michael Kors", "Bolsa Crossbody",               1299), ("Schutz",       "Clutch Couro",            399),
            ("Fossil",       "Relógio Masculino Grant",         899), ("Casio",        "G-Shock GA-2100",         699),
            ("Pandora",      "Pulseira com Charm",             799), ("Rip Curl",      "Relógio Surf",            499),
        ],
    },
    "Casa & Cozinha": {
        "Eletrodomésticos": [
            ("Tramontina",   "Air Fryer Digital 4L",           499), ("Philips",      "Airfryer XXL 7.3L",       899),
            ("Nespresso",    "Vertuo Next",                    699), ("Nespresso",    "Essenza Mini",            399),
            ("KitchenAid",   "Batedeira Stand Mixer",         1699), ("Oster",        "Liquidificador Triturax", 299),
            ("Tramontina",   "Panela de Pressão Elétrica",     599), ("Cuisinart",    "Processador de Alimentos", 799),
            ("Electrolux",   "Geladeira Frost Free 431L",     2999), ("Brastemp",     "Lavadora 11kg",          1999),
        ],
        "Utensílios de Cozinha": [
            ("Tramontina",   "Jogo de Panelas Inox 7pc",       799), ("Tramontina",   "Frigideira Antiaderente", 199),
            ("OXO",          "Conjunto Utensílios",             299), ("WMF",          "Faca Chef 20cm",          399),
            ("Arcos",        "Faca de Chef Profissional",      299), ("GoodGrips",     "Abridor de Latas",        149),
        ],
        "Organização & Armazenamento": [
            ("Arthi",        "Caixa Organizadora Com Tampa",   149), ("Tramontina",   "Porta-Tempero Giratório",  99),
            ("Ordene",       "Sapateira 4 Andares",            199), ("Coza",         "Balde com Tampa 20L",      89),
            ("Ordene",       "Organizador de Gaveta",           99), ("Arthi",        "Cabide Antideslizante",    49),
        ],
        "Decoração": [
            ("Tok&Stok",     "Vaso Decorativo Cerâmica",       299), ("Etna",         "Moldura Parede 30x40",    149),
            ("Madetê",       "Poltrona Decorativa Veludo",     899), ("Westwing",     "Tapete Persa 200x300",    599),
            ("Lights",       "Luminária de Mesa LED",          349), ("Leroy Merlin", "Prateleira Flutuante 80cm", 149),
        ],
        "Cama, Mesa & Banho": [
            ("Buddemeyer",   "Jogo de Cama Queen 300 Fios",   599), ("Karsten",      "Toalha de Banho Lumina",  199),
            ("Altenburg",    "Edredom Queen Pluma de Ganso",   799), ("Camesa",       "Travesseiro NASA",         299),
            ("Santista",     "Lençol Queen Matelasse",         399), ("Corttex",      "Jogo de Cama Solteiro",    249),
        ],
    },
    "Beleza & Saúde": {
        "Skincare": [
            ("La Roche-Posay","Protetor Solar Anthelios Toque Seco FPS60", 89),
            ("CeraVe",       "Hidratante Facial PM 52ml",       79), ("Neutrogena",   "Água Micelar Deep Clean", 49),
            ("La Roche-Posay","Effaclar Gel de Limpeza 300ml",   89), ("Vichy",       "Minéral 89 Concentrado",  129),
            ("Vult",         "Hidratante Facial Oil Free",       59), ("Simple",       "Gel de Limpeza Facial",    49),
            ("TRESemmé",     "Sérum Reparador de Pontas",        49), ("Pantogar",    "Suplemento Cabelos",       129),
        ],
        "Maquiagem": [
            ("MAC",          "Base Studio Fix Fluid",           249), ("Urban Decay",  "Paleta de Sombras Naked", 299),
            ("Maybelline",   "Máscara de Cílios Colossal",       69), ("NYX",         "Setting Spray",             89),
            ("Quem Disse",   "Batom Matte",                      59), ("L'Oréal",     "Base Infallible",           99),
            ("Vult",         "Blush Duo",                        49), ("Ruby Rose",   "Paleta de Contorno",        79),
        ],
        "Perfumes": [
            ("Natura",       "Essencial Oud Masculino 100ml",   299), ("O Boticário", "Malbec Elite 100ml",      299),
            ("Azzaro",       "Wanted 100ml",                    399), ("Paco Rabanne","Invictus 100ml",           399),
            ("Carolina Herrera","Good Girl 80ml",               599), ("Dior",       "Sauvage 60ml",              599),
            ("O Boticário", "Coffee Intense 100ml",             249), ("Eudora",      "Intense Gold 100ml",       199),
        ],
        "Cabelos": [
            ("Wella",        "Shampoo INVIGO Balance",          139), ("L'Oréal",     "Elseve Glycolic Gloss",    119),
            ("Pantene",      "Kit Restauração 3 Minutos",       149), ("Kerastase",   "Masque Fondant Nutritive",  299),
            ("TRESemmé",     "Shampoo Reconstrução",             79), ("OGX",         "Condicionador Argan Oil",   129),
            ("Amend",        "Progressiva Sem Formol",          129), ("Cadiveu",     "Plastica dos Fios 250ml",   299),
        ],
        "Saúde & Bem-estar": [
            ("Diprogenta",   "Creme Esfoliante Corporal",        89), ("Dove",        "Desodorante Invisible Dry", 39),
            ("Omron",        "Monitor de Pressão de Pulso",     299), ("G-Tech",      "Oxímetro de Dedo",         149),
            ("Multilaser",   "Balança Digital Bioimpedância",   299), ("Relaxbeauty", "Massageador de Pescoço",   199),
        ],
    },
    "Pets": {
        "Ração para Cães": [
            ("Royal Canin",  "Ração Medium Adult 15kg",         449), ("Royal Canin",  "Ração Large Adult 15kg",  499),
            ("Hills",        "Science Diet Adult 12kg",         399), ("Purina",       "Pro Plan Adult 15kg",     449),
            ("Golden",       "Ração Frango & Arroz 15kg",       279), ("Premier",      "Ração Nutrição Clínica",  369),
        ],
        "Ração para Gatos": [
            ("Royal Canin",  "Ração Indoor Adult 7.5kg",        299), ("Hills",        "Science Diet Sterilized",  399),
            ("Whiskas",      "Ração Adulto Carne 10.1kg",       199), ("Purina",       "Pro Plan Sterilized 7.5kg", 349),
            ("GoldenCat",    "Ração Premium Mix 7.5kg",         239), ("Premier",      "Ração Gatos Castrados",    299),
        ],
        "Acessórios Pet": [
            ("Furacão Pet",  "Cama Redonda com Capuz",         249), ("Jambo",        "Coleira Regulável L",       99),
            ("Chalesco",     "Arranhador Gato Sisal",          199), ("Pawise",       "Pet Carrier Mochila",      349),
            ("Kong",         "Brinquedo Recheável Médio",      149), ("Furacão Pet",  "Comedouro Automático",     599),
            ("Axon",         "Guia Retrátil 5m",               149), ("Zee.Dog",      "Coleira Neopreme M",       199),
        ],
    },
    "Livros & Cultura": {
        "Livros de Negócios": [
            ("HarperCollins","A Startup Enxuta — Eric Ries",        79), ("Sextante",    "O Jogo Infinito — Simon Sinek",     79),
            ("Intrínseca",   "Hábitos Atômicos — James Clear",      79), ("Alta Books",   "Zero To One — Peter Thiel",        79),
            ("Portfolio",    "Princípios — Ray Dalio",               89), ("Sextante",    "Mindset — Carol Dweck",            69),
            ("Campus",       "O Modelo Toyota — Liker",              89), ("Companhia das Letras","Sapiens — Harari",         69),
        ],
        "Ficção & Literatura": [
            ("Companhia das Letras","Mil Sóis Esplandecentes — Hosseini", 59),
            ("Rocco",        "O Senhor dos Anéis — Tolkien",         99), ("Intrínseca",   "Jogos Vorazes — Collins",          59),
            ("Sextante",     "Daisy Jones e The Six",                 59), ("Companhia das Letras","Duna — Herbert",          79),
            ("Planeta",      "As Sombras da Torre — King",            99), ("Suma",         "Lessons in Chemistry",            59),
        ],
        "Games": [
            ("Sony",         "PlayStation 5 825GB",              3999), ("Microsoft",   "Xbox Series X 1TB",        3999),
            ("Nintendo",     "Switch OLED",                      2799), ("Valve",       "Steam Deck OLED 1TB",       3799),
            ("Sony",         "God of War Ragnarok",               299), ("Rockstar",    "GTA VI",                    349),
            ("Nintendo",     "The Legend of Zelda: TotK",         349), ("Atlus",       "Persona 3 Reload",          249),
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════
# DESCRIPTION TEMPLATES (Portuguese seed content)
# The key to Vector Search — rich natural language with:
# - Usage context (without repeating the query term)
# - Implicit target audience
# - Functional benefits
# ══════════════════════════════════════════════════════════════════════

DESC_TEMPLATES = {
    "Smartphones": [
        """{marca} {modelo}: o companheiro ideal para quem vive conectado e não abre mão de performance no dia a dia. A câmera principal de alta resolução captura desde selfies casuais até fotos profissionais em qualquer condição de luz, incluindo ambientes noturnos com detalhes impressionantes. O processador de última geração garante multitarefa fluida, seja para reuniões de vídeo no trabalho, streaming de séries ou jogos mobile exigentes. A bateria de longa duração elimina a ansiedade de ficar sem carga durante o dia — ideal para quem passa horas fora de casa, viajantes frequentes e profissionais que dependem do celular para trabalhar. O design premium com acabamento fosco resiste a impressões digitais e oferece boa aderência na mão. Compatível com carregamento sem fio e carregamento rápido, o {modelo} é perfeito como presente de aniversário, formatura ou Natal para jovens, profissionais e entusiastas de tecnologia. Sistema operacional atualizado com suporte garantido por vários anos, ideal para quem busca um investimento duradouro em tecnologia mobile.""",

        """{modelo} da {marca}: a escolha certa para fotografia mobile de alta qualidade. Equipado com sistema de câmeras avançado com zoom óptico, é o aparelho favorito de criadores de conteúdo para redes sociais, blogueiros de viagem e quem ama registrar momentos especiais com qualidade de câmera dedicada. A tela com taxa de atualização alta proporciona rolagem suave e jogabilidade responsiva. Resistente à água e poeira, sobrevive a chuvas, piscinas e acidentes domésticos — perfeito para aventureiros, praticantes de esportes aquáticos e quem trabalha em ambientes externos. A conectividade 5G garante velocidade máxima em redes de nova geração. Uma excelente escolha para presentear alguém que esteja trocando de aparelho ou que tenha o celular antigo quebrado.""",
    ],
    "Notebooks": [
        """{modelo} da {marca}: a máquina perfeita para trabalho, estudo e criatividade. Com processador de alta performance e memória RAM abundante, executa múltiplos aplicativos simultaneamente sem perder velocidade — ideal para programadores, designers, editores de vídeo e profissionais que dependem de software pesado. A tela de alta resolução com cobertura ampla de cores reproduz imagens com fidelidade, essencial para trabalho com design gráfico, edição de fotos e consumo de conteúdo. A bateria de longa duração aguenta um dia inteiro de uso sem precisar de tomada — a solução para quem trabalha em cafés, bibliotecas ou em home office sem fio sempre disponível. O teclado confortável com teclas de course adequado reduz a fadiga em longas sessões de digitação. Excelente para estudantes universitários, profissionais em transição para home office e qualquer pessoa que precise de um computador confiável para o dia a dia.""",

        """O {marca} {modelo} é a escolha premium para quem não aceita compromissos entre portabilidade e desempenho. Ultrafino e leve, cabe em qualquer mochila sem pesar nas costas — ideal para quem se desloca com frequência entre reuniões, viaja a trabalho ou estuda em diferentes locais. A construção robusta em alumínio premium suporta o uso intenso do cotidiano. O SSD de alta velocidade inicializa o sistema em segundos e abre aplicativos instantaneamente, eliminando a espera que reduz a produtividade. Compatível com monitores externos, permite montar uma estação de trabalho completa em casa e voltar à mobilidade no escritório. Uma das melhores opções para presentear estudantes que entram na faculdade, jovens profissionais e freelancers.""",
    ],
    "Fones de Ouvido": [
        """{modelo} da {marca}: a experiência sonora definitiva para quem valoriza qualidade de áudio no dia a dia. O cancelamento ativo de ruído bloqueia o barulho do metrô, escritório aberto, avião e rua — permitindo foco total durante estudos, trabalho ou simplesmente aproveitar a música sem distração. Os drivers premium reproduzem graves profundos e agudos cristalinos, próximos da experiência de um estúdio profissional. O microfone com supressão de ruído garante chamadas de vídeo e reuniões online com voz clara mesmo em ambientes ruidosos. A bateria de longa duração aguenta voos internacionais e dias de uso intenso. Confortável para uso prolongado graças às almofadas macias e headband ajustável. Perfeito para: músicos, fãs de podcasts, profissionais em home office e quem passa horas ouvindo música.""",

        """Os fones {marca} {modelo} entregam liberdade e qualidade sonora em formato compacto. O design intra-auricular com ajuste seguro é ideal para corridas, treinos de musculação, ciclismo e atividades físicas intensas — ficam no lugar mesmo com movimento brusco. À prova d'água, resistem ao suor do treino e à chuva do dia a dia. O som imersivo com perfil acústico calibrado entrega batidas que motivam durante o exercício ou trilhas detalhadas para quem curte audiobook na academia. A conectividade Bluetooth de baixa latência elimina o atraso entre imagem e som durante filmes e games. Excelente para presentear praticantes de esportes, runners, ciclistas e qualquer pessoa que queira qualidade sonora sem fio.""",
    ],
    "Smart TVs": [
        """{modelo} da {marca}: a televisão que transforma a sala em uma experiência cinematográfica. O painel {modelo} com resolução 4K Ultra HD entrega imagem com quatro vezes mais detalhes que o Full HD, revelando texturas, cores e sombras que passam despercebidos em telas comuns. O sistema de som integrado com Dolby Atmos reproduz áudio tridimensional sem precisar de soundbar adicional — perfeito para quem monta um home theater completo. O sistema operacional smart integra Netflix, Prime Video, Disney+, Globoplay e dezenas de outros apps nativamente, sem precisar de dispositivos externos. Compatível com controle por voz e com assistentes virtuais para mudar de canal, ajustar volume e pesquisar conteúdo com os comandos de voz. Ideal para famílias que querem qualidade de cinema em casa, fãs de futebol que acompanham jogos com imagem perfeita e entusiastas de séries.""",
    ],
    "Tênis Esportivos": [
        """{modelo} da {marca}: desenvolvido para atletas que exigem performance em cada passada. A tecnologia de amortecimento reativo absorve o impacto do calcanhar e devolve energia ao avanço, reduzindo o cansaço muscular durante corridas de longa distância como meia maratona e maratona. A entressola responsiva proporciona sensação de leveza mesmo após vários quilômetros rodados. A sola de borracha com padrão de tração multidirecional oferece aderência confiável em asfalto, calçadas e trilhas leves. O cabedal em mesh respirável mantém os pés frescos durante treinos intensos, prevenindo bolhas e desconforto. Recomendado por fisioterapeutas para corredores com pisada neutra e supinada. Perfeito para quem está se preparando para uma corrida de rua, para treinos diários de resistência cardiovascular e para quem simplesmente quer um tênis confortável para caminhar e praticar exercícios variados. Ótimo presente para quem está começando a correr.""",

        """O {marca} {modelo} é o tênis escolhido por corredores que buscam versatilidade sem abrir mão do desempenho. Funciona igualmente bem na esteira da academia, no asfalto da cidade e em trilhas curtas de terra, tornando-o o companheiro ideal para quem varia o tipo de treino. O amortecimento equilibrado entre responsividade e conforto agrada tanto iniciantes quanto atletas experientes. O design urbano permite usá-lo também em situações casuais — academia, mercado, faculdade — sem parecer exagerado. A palmilha removível facilita a higienização e é compatível com palmilhas ortopédicas para quem tem alguma necessidade específica. Uma das escolhas mais populares entre personal trainers, praticantes de funcional e quem busca um tênis de uso geral para o dia a dia ativo.""",
    ],
    "Equipamentos de Musculação": [
        """{modelo}: o equipamento essencial para quem está montando uma academia em casa ou quer complementar os treinos sem depender de academia convencional. O design prático permite armazenar facilmente em apartamentos pequenos sem tomar muito espaço. Material de alta durabilidade suporta uso intenso por anos sem perder a forma ou qualidade. Versátil para dezenas de exercícios diferentes — agachamento, supino, remada, desenvolvimento, rosca bíceps — permite trabalhar diferentes grupos musculares com apenas um equipamento. Ideal para treinos de musculação, funcional, HIIT e reabilitação orientada por fisioterapeuta. Ótima opção para quem quer manter a rotina de exercícios em viagens ou para presentear alguém que está começando a academia em casa. Frete grátis para a maioria das regiões.""",
    ],
    "Suplementos": [
        """{modelo}: suplemento desenvolvido para quem leva a sério a nutrição esportiva e busca resultados consistentes nos treinos. A proteína de alta qualidade apoia a recuperação muscular após sessões intensas de musculação, crossfit, corrida e esportes coletivos, garantindo que o músculo tenha os aminoácidos necessários para crescer e se recuperar. Cada dose fornece proteínas com alto valor biológico, ideal para consumir após o treino ou entre refeições. Disponível em sabores variados que facilitam a adesão à rotina de suplementação. Indicado por nutricionistas esportivos para atletas amadores e profissionais que buscam ganho de massa magra ou manutenção muscular em processo de emagrecimento. Ótimo presente para quem pratica atividades físicas com frequência ou está iniciando uma dieta com acompanhamento profissional.""",
    ],
    "Roupas Esportivas": [
        """{modelo} da {marca}: peça desenvolvida com tecido técnico de alta performance que acompanha cada movimento do treino. O tecido dry-fit afasta a umidade do corpo, mantendo a pele seca durante exercícios de alta intensidade como corrida, ciclismo indoor, crossfit e musculação. A costura plana evita atrito e irritação na pele em movimentos repetitivos. O design anatômico molda ao corpo sem apertar, garantindo liberdade total de movimentos em agachamentos, burpees e movimentos de levantamento. A proteção UV é um diferencial para atividades ao ar livre como corrida de rua, ciclismo e esportes de quadra. Indicada para treinos diários, competições amadores e uso casual esportivo. Fácil de lavar e seca rapidamente, prática para manter a rotina de exercícios sem acúmulo de peças sujas.""",
    ],
    "Roupas Masculinas": [
        """{modelo} da {marca}: a peça que eleva qualquer look sem esforço. O caimento {modelo} valoriza a silhueta sem apertar, confortável para usar durante longas jornadas de trabalho, reuniões de negócios, almoços e eventos sociais. O tecido premium com composição especial amassa pouco e recupera a forma rapidamente, ideal para viagens de negócios onde não há tempo para passar roupa. A confecção detalhada com costuras reforçadas garante durabilidade mesmo com uso e lavagem frequentes. Versátil o suficiente para compor looks formais com calça social e sapato, e mais descontraídos com jeans e tênis. Uma das peças favoritas de homens que valorizam estilo sem complicação no dia a dia. Ótima opção para presentear no Dia dos Pais, aniversários e formaturas.""",
    ],
    "Roupas Femininas": [
        """{modelo} da {marca}: a peça que toda mulher precisa no guarda-roupa. O design versátil permite compor looks para diferentes ocasiões — do casual do final de semana ao semi-formal para o trabalho, com apenas uma mudança de acessórios. O caimento é pensado para valorizar diferentes biotipos, com modelagem que libera o movimento e não marca o corpo de forma incômoda. O tecido de qualidade mantém a cor após várias lavagens e não perde a forma com o uso. A estampa exclusiva e exclusiva garante elegância com personalidade, fugindo do básico sem exagerar. Perfeito para mulheres que querem praticidade sem abrir mão de estilo. Uma das peças mais presentes nas listas de Dia das Mães, aniversários e auto-presente para mulheres que querem renovar o guarda-roupa.""",
    ],
    "Calçados Masculinos": [
        """{modelo} da {marca}: o calçado que atravessa décadas de moda sem envelhecer. O design icônico combina com praticamente qualquer peça do guarda-roupa — jeans, calça de moletom, shorts, calça alfaiataria — tornando-o um dos mais versáteis da categoria. A construção robusta com materiais de qualidade garante durabilidade para uso diário sem perder a estética. A sola antiderrapante oferece segurança em diferentes superfícies, seja no calçamento da cidade, no escritório ou em eventos casuais. Conforto para uso prolongado com o amortecimento adequado para quem passa o dia em pé ou caminhando bastante. Um dos presentes mais certeiros para homens de qualquer faixa etária, do adolescente ao executivo que aprecia um bom streetwear.""",
    ],
    "Calçados Femininos": [
        """{modelo} da {marca}: o calçado que une conforto e elegância de forma que parecia impossível. A palmilha anatomicamente moldada ao pé reduz o cansaço mesmo após horas de uso, essencial para quem trabalha em pé, participa de eventos longos ou simplesmente caminha bastante. O material de qualidade amacia com o uso, adaptando ao formato único de cada pé. O design atemporal complementa tanto looks mais formais — vestidos e calças alfaiataria — quanto combinações casuais com jeans e blazer. Disponível em cores neutras e vibrantes para atender diferentes personalidades e ocasiões. Entre os preferidos em listas de Dia das Mães, presentes de aniversário para amigas e presenteamento próprio para mulheres que apreciam calçados de qualidade.""",
    ],
    "Eletrodomésticos": [
        """{modelo} da {marca}: o eletrodoméstico que revoluciona a rotina na cozinha. A tecnologia avançada reduz drasticamente o tempo de preparo sem abrir mão do sabor e da textura que métodos tradicionais entregam. Econômico em energia e prático de limpar, tornou-se indispensável em apartamentos pequenos, famílias com rotina corrida e cozinheiros amadores que querem praticidade sem sacrificar a alimentação saudável. Perfeito para preparar desde pratos simples do dia a dia até receitas mais elaboradas para receber amigos em casa. O design compacto economiza espaço no balcão sem comprometer a capacidade. Um dos eletrodomésticos mais presenteados em casamentos, chá de cozinha e Natal. Acompanha livro de receitas exclusivas para aproveitar ao máximo o potencial do equipamento.""",
    ],
    "Utensílios de Cozinha": [
        """{modelo} da {marca}: utensílio que eleva a qualidade do preparo e da experiência na cozinha. Desenvolvido com materiais de grau profissional adaptados ao uso doméstico, oferece durabilidade e performance superiores às opções comuns encontradas em supermercados. O design ergonômico facilita o manuseio mesmo por quem não tem experiência culinária avançada. Compatível com diferentes tipos de fogão, incluindo indução, garantindo versatilidade para diferentes cozinhas. Fácil de lavar, alguns modelos são seguros para máquina de lavar louça. Presente certeiro para quem está montando o enxoval de casa, noivos, amantes de culinária e cozinheiros amadores que querem dar um upgrade nos utensílios.""",
    ],
    "Skincare": [
        """{modelo} da {marca}: o cuidado que sua pele merece no dia a dia. Formulado com ativos de eficácia comprovada por dermatologistas, trata e previne as principais queixas da pele — ressecamento, manchas, oleosidade, linhas de expressão e falta de luminosidade. A textura leve de rápida absorção não deixa resíduo gorduroso, tornando-o adequado para uso sob maquiagem e para peles sensíveis. O pH balanceado respeita a barreira natural da pele, evitando irritação e ressecamento. Dermatologicamente testado e aprovado para uso diário em todas as estações do ano. Indicado por especialistas em skincare para quem está montando uma rotina de cuidados eficiente sem exagero de produtos. Um dos mais presenteados em kits de autocuidado, cestas de Natal e presentes para amigos que curtem beleza.""",
    ],
    "Perfumes": [
        """{modelo} da {marca}: uma fragrância que marca presença e cria memórias. A composição olfativa equilibra notas de abertura frescas, coração marcante e fundo de longa duração que permanece na pele por horas e nas roupas por dias. A projeção moderada garante presença sem invadir o espaço das pessoas ao redor, ideal para uso no trabalho, eventos sociais e encontros. O frasco sofisticado agrega valor visual ao presentear, dispensando embrulho elaborado. Adequado para uso em diferentes estações e ocasiões — do dia a dia casual às noites especiais. Entre os perfumes mais presenteados no Brasil em datas como Dia dos Namorados, Natal, Dia das Mães e Dia dos Pais. Combina com diferentes personalidades e estilos, tornando-o uma escolha segura para presentear pessoas próximas.""",
    ],
    "Ração para Cães": [
        """{modelo} da {marca}: nutrição completa desenvolvida por veterinários e nutricionistas especializados em saúde canina. A formulação balanceada atende todas as necessidades nutricionais de cães adultos de porte {modelo}, incluindo proteínas de alta qualidade para manutenção da musculatura, ômega 3 e 6 para pelo brilhante e saúde da pele, prebióticos para saúde digestiva e vitaminas para imunidade. A crocância dos grãos auxilia na saúde bucal, reduzindo o acúmulo de tártaro. Palatável e com alto índice de aceitação por cães exigentes — ideal para tutores que lutam para que o pet aceite a alimentação adequada. Recomendada por veterinários e petshops especializados como uma das melhores opções custo-benefício disponíveis no mercado nacional. Embalagem resistente à umidade para manter a qualidade e o aroma até o último grão.""",
    ],
    "Ração para Gatos": [
        """{modelo} da {marca}: formulada especialmente para as necessidades únicas dos felinos. A alta concentração de proteína animal atende ao perfil carnívoro obrigatório dos gatos, suportando saúde muscular, energia e longevidade. Enriquecida com taurina para saúde cardíaca e visual — nutriente essencial que gatos não sintetizam naturalmente. O teor controlado de minerais auxilia na prevenção de cálculos urinários, uma das principais causas de problemas de saúde em gatos domésticos. O aroma irresistível estimula gatos com apetite reduzido ou seletivos. Indicada por veterinários como ração de alta qualidade para gatos de interior, castrados e com sobrepeso. Tutor de gato que prioriza saúde e bem-estar do felino escolhe nutrição premium.""",
    ],
    "Acessórios Pet": [
        """{modelo} da {marca}: acessório que melhora o dia a dia de pets e tutores. Desenvolvido com materiais seguros e não tóxicos, aprovados para contato com animais de diferentes portes e idades. O design confortável respeita o bem-estar do animal, sem pontos de pressão ou material que possa causar desconforto. Prático para tutores que valorizam funcionalidade sem abrir mão de estilo — disponível em cores e tamanhos variados para personalizar a experiência do pet. Ótima opção para presentear tutores de cães e gatos em aniversários, Natal e datas especiais. Durável para uso diário, lavável e de fácil higienização. Aprovado por pet influencers e recomendado em grupos de tutores conscientes que buscam o melhor para seus companheiros.""",
    ],
    "Livros de Negócios": [
        """{modelo}: uma das obras mais transformadoras para profissionais que buscam evolução na carreira e nos negócios. O autor compila décadas de pesquisa e experiência em conceitos práticos e aplicáveis a partir do primeiro capítulo — sem teoria desnecessária. Recomendado por empreendedores, líderes corporativos, estudantes de MBA e qualquer pessoa que queira pensar de forma mais estratégica sobre trabalho, liderança e produtividade. A linguagem direta e os casos reais facilitam a absorção das ideias, tornando a leitura produtiva mesmo para quem não tem o hábito de ler livros técnicos. Um dos mais presenteados em ambientes corporativos, bootcamps de startups e para jovens profissionais em início de carreira. Leitura obrigatória para quem quer sair do operacional e pensar de forma mais estratégica.""",
    ],
    "Ficção & Literatura": [
        """{modelo}: uma experiência literária que prende o leitor do início ao fim. A narrativa envolvente combina construção de personagens profunda, enredo imprevisível e temas universais que ressoam com diferentes tipos de leitores — do casual ao fanático literário. Tradução cuidadosa preserva o ritmo e a voz original do autor. Indicado por grupos de leitura, recomendado em listas de melhores livros do ano e discutido em bookclubs de todo o país. Perfeito para quem quer retomar o hábito da leitura ou está em busca de uma obra que tire o fôlego. Uma das melhores opções para presentear apaixonados por literatura, amigos que adoram um bom romance e para quem busca escapismo inteligente em uma boa história.""",
    ],
    "Games": [
        """{modelo} da {marca}: a experiência de entretenimento interativo mais imersiva da geração. Os gráficos de última geração com ray-tracing em tempo real criam cenários fotorrealistas que apagam a linha entre jogo e cinema. O desempenho fluido em alta taxa de quadros garante jogabilidade responsiva e precisa, essencial tanto para jogos de ação quanto para RPGs de exploração aberta. O sistema de áudio espacial 3D posiciona sons com precisão, aumentando o senso de presença e imersão. Compatível com a biblioteca crescente de títulos exclusivos e multiplataforma. Ideal para gamers que levam a sério a experiência ou como presente premium para adolescentes, jovens adultos e entusiastas de jogos eletrônicos. Uma das escolhas mais desejadas em listas de Natal e aniversários.""",
    ],
    "Bolsas & Acessórios": [
        """{modelo} da {marca}: o acessório que completa qualquer look com personalidade. O design cuidadoso equilibra elegância e funcionalidade — compartimentos bem organizados acomodam o essencial do dia a dia sem deixar o item sobrecarregado e volumoso. O material de qualidade envelhece bem, ganhando personalidade com o uso em vez de deteriorar. Versátil para diferentes ocasiões — trabalho, passeio, viagem e eventos — sem precisar trocar de bolsa várias vezes no dia. Entre os presentes favoritos para aniversários de mulheres, Dia das Mães e auto-presentes de quem quer renovar os acessórios sem ir ao exagero. O acabamento premium posiciona o item acima das opções básicas, entregando status e durabilidade pelo preço justo.""",
    ],
    "Câmeras": [
        """{modelo} da {marca}: para quem quer ir além do smartphone na fotografia. O sensor de alta resolução captura detalhes impossíveis de reproduzir em celulares, especialmente em condições de pouca luz e em cenas de alta velocidade. O sistema de foco automático avançado rastreia olhos de pessoas e animais com precisão, ideal para fotografia de retratos, pets e eventos. Compatível com vasta linha de objetivas intercambiáveis, permitindo crescer junto com a câmera e explorar desde grande angular até telefoto. O sistema de estabilização de imagem integrado elimina tremidos em vídeos filmados à mão. Indicada para fotógrafos amadores que querem evoluir seriamente, criadores de conteúdo que precisam de imagem profissional e profissionais liberais que querem produzir seu próprio material visual.""",
    ],
    "Smartwatches": [
        """{modelo} da {marca}: muito mais do que um relógio, um parceiro de saúde e produtividade no pulso. O monitoramento contínuo de frequência cardíaca detecta variações anormais e alerta para possíveis situações de risco. O rastreamento de sono analisa as fases e sugere melhorias para a qualidade do descanso. O GPS integrado registra rotas de corrida, trilha e ciclismo com precisão, dispensando o celular durante o exercício. Notificações de mensagens, chamadas e apps sociais chegam direto ao pulso, reduzindo a necessidade de pegar o celular com frequência. À prova d'água, acompanha nados e atividades aquáticas. Um dos presentes favoritos para esportistas, pessoas que querem acompanhar a saúde de forma proativa e profissionais que buscam mais produtividade e foco.""",
    ],
    "Tablets": [
        """{modelo} da {marca}: a tela que expande as possibilidades do dia a dia. A tela ampla com alta resolução é perfeita para consumo de conteúdo em qualidade superior — séries, filmes e vídeos em detalhes que o celular não consegue reproduzir. Para estudantes, a combinação com teclado e caneta digital (vendidos separadamente) transforma o tablet em um substituto do notebook para anotações, pesquisa e trabalho. Artistas e designers aproveitam a tela sensível à pressão para ilustração e design digital. A autonomia de bateria generosa aguenta um dia inteiro de uso intenso. Ideal para presentear jovens estudantes, profissionais criativos e qualquer pessoa que queira ter uma tela maior para o lazer e a produtividade.""",
    ],
    "Bikes & Ciclismo": [
        """{modelo} da {marca}: para quem quer pedalar mais, seja por esporte, lazer ou transporte urbano sustentável. O quadro em {modelo} equilibra rigidez e leveza, garantindo que cada pedalada seja aproveitada com eficiência máxima e sem vibração excessiva que cansa os braços em trilhas. O conjunto de marchas preciso permite adaptar o esforço a qualquer tipo de terreno — subidas íngremes, descidas rápidas e trechos planos de velocidade cruzeira. Os freios a disco oferecem frenagem controlada em qualquer condição climática, incluindo chuva e lama. Indicada para iniciantes que querem uma bike de qualidade para crescer junto e para intermediários que estão evoluindo o equipamento. Ótima opção de presente para ciclistas, entusiastas de vida saudável e quem quer substituir o carro nos deslocamentos curtos.""",
    ],
    "Saúde & Bem-estar": [
        """{modelo} da {marca}: cuide da sua saúde com praticidade e precisão. O monitoramento fácil e rápido permite acompanhar indicadores importantes de saúde no conforto de casa, sem precisar ir a farmácias ou clínicas para verificações simples de rotina. Os resultados precisos e de fácil leitura são ideais para compartilhar com o médico no acompanhamento regular. Design ergonômico confortável para pessoas de todas as idades, incluindo idosos. Compacto o suficiente para guardar na bolsa e usar em viagens. Indispensável para pessoas com condições crônicas que precisam de monitoramento regular, para idosos que moram sozinhos e para famílias que querem ter o controle da saúde em casa. Um dos presentes mais úteis e duradouros para pais e avós.""",
    ],
    "Maquiagem": [
        """{modelo} da {marca}: maquiagem de alta performance para quem leva a make a sério. A fórmula desenvolvida por profissionais do mercado de beleza entrega cobertura e durabilidade que resistem ao calor, umidade e ao dia longo sem retoque. O acabamento sofisticado reproduzido é o favorito de maquiadores profissionais em editoriais, casamentos e eventos. A paleta de tonalidades inclusiva atende diferentes tons de pele, do mais claro ao mais escuro, com fórmulas desenvolvidas para valorizar cada subtom. Cruelty-free e dermatologicamente testado, adequado para peles sensíveis e olhos sensíveis. Muito presenteado em datas como Natal, aniversários e kits de autocuidado para amigas que amam beleza e make artística.""",
    ],
    "Cabelos": [
        """{modelo} da {marca}: tratamento capilar desenvolvido para quem quer cabelos saudáveis sem precisar de salão toda semana. A fórmula concentrada com ativos reparadores penetra na fibra capilar danificada pelo calor, tinturas e processos químicos, reconstruindo o interior do fio e devolvendo a maciez e o brilho natural. O uso regular reduz o frizz, facilita o desembaraço e prolonga a vida da coloração. Indicado por cabeleireiros profissionais para cabelos secos, quebradiços e com pontas duplas. O aroma agradável permanece nos fios após a lavagem, tornando a experiência ainda mais satisfatória. Perfeito para montar kits de presente para amigas e familiares que investem nos cabelos, para salões que querem oferecer tratamento profissional em casa.""",
    ],
    "Natação & Aquáticos": [
        """{modelo} da {marca}: equipamento desenvolvido para otimizar o treino na piscina. O design hidrodinâmico reduz a resistência da água, permitindo maior velocidade com menos esforço — essencial para atletas que buscam melhorar o tempo em cada distância. O material de alta qualidade resiste ao cloro das piscinas sem desgastar ou perder a forma, garantindo durabilidade muito superior ao material comum. Confortável para sessões longas sem causar irritação na pele ou nos olhos. Indicado para praticantes de natação recreativa, atletas amadores e nadadores competitivos de todas as idades. Ótima opção para presentear quem acabou de iniciar aulas de natação ou para nadadores experientes que querem evoluir o equipamento.""",
    ],
    "default": [
        """{modelo} da {marca}: a escolha certa para quem valoriza qualidade e quer praticidade no dia a dia. Desenvolvido com materiais de primeira linha e controle rigoroso de qualidade, oferece desempenho superior às opções genéricas disponíveis no mercado. O design pensado para o usuário brasileiro considera o clima, os hábitos e as necessidades locais, entregando uma experiência adaptada ao contexto nacional. Durável e de fácil manutenção, mantém a qualidade original por muito tempo com os cuidados básicos recomendados pelo fabricante. Disponível para entrega rápida em todo o Brasil com garantia do fabricante. Um dos produtos mais bem avaliados da categoria pelos compradores que já tiveram a experiência. Ótima opção para uso próprio ou para presentear pessoas próximas em datas especiais.""",
    ],
}

# ══════════════════════════════════════════════════════════════════════
# DESCRIPTION VARIANTS — second axis of variation
# Without this, every product in a subcategoria that only has ONE base
# template (most of them) shares near-identical descricao text, which ties
# the Atlas Search lexical score across dozens/hundreds of documents for any
# query whose terms live only in the shared boilerplate — collapsing
# $rankFusion rank to 1 for all of them and $scoreFusion's minMaxScaler to
# 0/0 (see docs/adr/0001). One extra randomly-picked sentence, combined with
# the base template choice, multiplies the number of distinct texts per
# subcategoria and — for "Tênis Esportivos" specifically — varies the actual
# frequency of query-relevant terms (corrida, treino), which is what
# actually moves a BM25 score, not just adding unrelated words.
# ══════════════════════════════════════════════════════════════════════

DESC_DIFERENCIAIS = {
    "Tênis Esportivos": [
        "Ideal também para treinos de corrida de rua e provas de 5km, 10km e meia maratona.",
        "Testado por corredores amadores em treinos de corrida de longa distância no asfalto.",
        "Recomendado para caminhada rápida, corrida leve e treino funcional intercalado.",
        "Aprovado por atletas de corrida de trilha (trail running) em terrenos irregulares.",
        "Popular entre iniciantes na corrida que estão comprando o primeiro tênis técnico.",
        "Usado em provas de corrida de rua por atletas amadores e semiprofissionais da modalidade.",
    ],
    "default": [
        "Entrega rápida disponível para todo o Brasil, com rastreamento em tempo real do pedido.",
        "Produto com garantia estendida do fabricante e nota alta de avaliação entre compradores.",
        "Frequentemente comprado em promoções relâmpago e datas comemorativas do e-commerce.",
        "Estoque limitado — um dos itens mais procurados da categoria neste mês na loja.",
        "Recomendado por clientes que já compraram produtos similares nesta mesma loja.",
        "Ótimo custo-benefício frente a opções concorrentes na mesma faixa de preço no mercado.",
    ],
}

CORES = ["Preto", "Branco", "Azul", "Vermelho", "Verde", "Cinza", "Dourado", "Rosa", "Roxo", "Laranja", "Bege", "Prata"]
TAMANHOS_ROUPA  = ["PP", "P", "M", "G", "GG", "XGG"]
TAMANHOS_CALÇADO = [str(n) for n in range(34, 46)]
TAMANHOS_GENERICO = ["Único", "P", "M", "G"]

CONDICOES  = ["Novo", "Novo", "Novo", "Novo", "Reembalado", "Recondicionado"]
GENEROS    = ["Masculino", "Feminino", "Unissex"]
VENDEDORES = [
    "Loja Oficial", "Magazine Luiza", "Americanas", "Casas Bahia",
    "Shopee Store", "MercadoLivre Premium", "Amazon.com.br",
    "Netshoes", "Dafiti", "FastShop", "Kabum", "Extra.com.br",
]

AVALIACOES_TEXTO = [
    "Produto excelente! Superou minhas expectativas. Recomendo muito.",
    "Ótima compra. Chegou rápido e embalado com cuidado. Exatamente como na descrição.",
    "Bom produto, mas a entrega demorou mais do que o esperado. O produto em si é muito bom.",
    "Comprei como presente e a pessoa amou! Qualidade incrível pelo preço.",
    "Já é o segundo que compro. Produto de ótima qualidade e durabilidade comprovada.",
    "Muito bom! A foto condiz com o produto real. Material de qualidade superior.",
    "Produto chegou antes do prazo e em perfeito estado. Atendimento nota 10.",
    "Excelente custo-benefício. Não esperava tanta qualidade por este preço.",
    "Cumpre o que promete. Uso há 3 meses e continua perfeito.",
    "Produto top! Só senti falta de uma instrução mais detalhada em português.",
    "Qualidade muito boa, mas o tamanho ficou um pouco diferente do esperado. Fique atento às medidas.",
    "Perfeito para o meu uso. Chegou em 2 dias. Embalagem cuidadosa.",
    "Produto de alta qualidade. Parece muito mais caro do que é. Recomendo sem hesitar.",
    "Comprei para uso profissional e estou muito satisfeito com o resultado.",
    "Bom, mas já encontrei outros similares mais baratos. Funciona bem no dia a dia.",
]

TITULOS_AVALIACAO = [
    "Produto incrível!", "Valeu cada centavo", "Recomendo muito",
    "Excelente compra", "Surpreendeu positivamente", "Qualidade top",
    "Produto bom", "Chegou rápido", "Ótimo para o uso", "Nota 10",
    "Poderia ser melhor", "Entrega rápida", "Muito satisfeito",
    "Presente perfeito", "Comprei de novo",
]

# ══════════════════════════════════════════════════════════════════════
# DOCUMENT GENERATION
# ══════════════════════════════════════════════════════════════════════

def get_descricao(subcategoria: str, marca: str, modelo: str, cor: str, tamanho: str,
                   genero: str, avaliacao: float, preco: float) -> str:
    templates = DESC_TEMPLATES.get(subcategoria, DESC_TEMPLATES["default"])
    tmpl = random.choice(templates)
    base = tmpl.format(marca=marca, modelo=modelo).strip()
    # Per-unit closing clause: without this, every product sharing a subcategoria
    # produces near-identical text (only marca/modelo differ), which ties the
    # Atlas Search lexical score across dozens of documents — and a tied score
    # collapses $rankFusion rank to 1 for all of them and $scoreFusion's
    # minMaxScaler to 0/0 for all of them (see docs/adr/0001, Sintoma 1/2).
    extra = (
        f" Esta unidade na cor {cor.lower()}, tamanho {tamanho}, indicada para "
        f"público {genero.lower()}, tem avaliação média de {avaliacao:.1f} entre "
        f"compradores e sai por R$ {preco:.2f}."
    )
    diferenciais = DESC_DIFERENCIAIS.get(subcategoria, DESC_DIFERENCIAIS["default"])
    diferencial = " " + random.choice(diferenciais)
    return base + extra + diferencial

def get_tamanho(subcategoria: str, categoria: str) -> str:
    if "Tênis" in subcategoria or "Calçado" in subcategoria:
        return random.choice(TAMANHOS_CALÇADO)
    if "Roupa" in subcategoria or "Moda" in categoria:
        return random.choice(TAMANHOS_ROUPA)
    return random.choice(TAMANHOS_GENERICO)

def random_date(start=datetime(2023, 1, 1), end=datetime(2026, 5, 1)) -> datetime:
    return start + timedelta(seconds=random.randint(0, int((end - start).total_seconds())))

def make_produto(categoria: str, subcategoria: str) -> dict:
    marca, modelo, preco_base = random.choice(CATALOGO[categoria][subcategoria])

    desconto = random.randint(0, 40)
    preco_orig = round(preco_base * random.uniform(0.9, 1.3), 2)
    preco_final = round(preco_orig * (1 - desconto / 100), 2)

    avaliacao = round(random.uniform(3.2, 5.0), 1)
    tot_aval  = random.randint(0, 15000)

    cor     = random.choice(CORES)
    tamanho = get_tamanho(subcategoria, categoria)
    genero  = random.choice(GENEROS)

    descricao = get_descricao(subcategoria, marca, modelo, cor, tamanho, genero,
                               avaliacao, preco_final)

    return {
        "produto_id":      str(uuid.uuid4()),
        "nome":            f"{marca} {modelo} — {cor}",
        "marca":           marca,
        "modelo":          modelo,
        "categoria":       categoria,
        "subcategoria":    subcategoria,
        "descricao":       descricao,
        "preco":           preco_final,
        "preco_original":  preco_orig,
        "desconto_pct":    desconto if desconto > 0 else None,
        "avaliacao_media": avaliacao,
        "total_avaliacoes":tot_aval,
        "em_estoque":      random.random() > 0.08,
        "condicao":        random.choice(CONDICOES),
        "genero":          genero,
        "atributos": {
            "cor":     cor,
            "tamanho": tamanho,
        },
        "vendedor":    random.choice(VENDEDORES),
        "sku":         f"{marca[:3].upper()}-{modelo[:4].upper()}-{cor[:2].upper()}-{tamanho}",
        "created_at":  random_date(),
    }

def make_avaliacao(produto_id: str, categoria: str) -> dict:
    nota = random.choices([1, 2, 3, 4, 5], weights=[2, 4, 10, 25, 59])[0]
    return {
        "produto_id": produto_id,
        "categoria":  categoria,
        "usuario":    f"usuario_{random.randint(100000, 999999)}",
        "nota":       nota,
        "titulo":     random.choice(TITULOS_AVALIACAO),
        "texto":      random.choice(AVALIACOES_TEXTO),
        "verificado": random.random() > 0.3,
        "util_count": random.randint(0, 500),
        "data":       random_date(),
    }

# Flattened category pool for fast sampling
ALL_ITEMS = []
for cat, subcats in CATALOGO.items():
    for sub in subcats:
        ALL_ITEMS.append((cat, sub))

# ══════════════════════════════════════════════════════════════════════
# PROGRESS & INSERTION
# ══════════════════════════════════════════════════════════════════════

def progress_bar(current, total, start_ts, bar_width=38, label=""):
    pct     = current / total
    filled  = int(bar_width * pct)
    bar     = "█" * filled + "░" * (bar_width - filled)
    elapsed = time.time() - start_ts
    speed   = current / elapsed if elapsed > 0 else 0
    eta     = (total - current) / speed if speed > 0 else 0
    print(
        f"\r  [{bar}] {pct*100:5.1f}%  "
        f"{current:>10,}/{total:,}  "
        f"{speed:>7,.0f} docs/s  "
        f"ETA {eta:>5.0f}s  {label}",
        end="", flush=True
    )

def insert_bulk(col, batch: list):
    try:
        col.insert_many(batch, ordered=False)
    except BulkWriteError:
        pass

def populate_produtos(db, total):
    col = db[COL_PRODUTOS]
    inserted, start_ts = 0, time.time()
    print(f"\n  ▶ {COL_PRODUTOS} — {total:,} docs\n")

    # Keep product_ids to reuse when generating reviews
    sample_ids = []

    while inserted < total:
        n     = min(BATCH_SIZE, total - inserted)
        batch = []
        for _ in range(n):
            cat, sub = random.choice(ALL_ITEMS)
            doc = make_produto(cat, sub)
            batch.append(doc)
            # Collect ~2% of the ids for reviews
            if len(sample_ids) < 200_000 and random.random() < 0.02:
                sample_ids.append((doc["produto_id"], doc["categoria"]))
        insert_bulk(col, batch)
        inserted += n
        progress_bar(inserted, total, start_ts, label=COL_PRODUTOS)

    elapsed = time.time() - start_ts
    print(f"\n  ✅ {COL_PRODUTOS} — {inserted:,} em {elapsed:.1f}s ({inserted/elapsed:,.0f}/s)\n")
    return sample_ids

def populate_vector_sample(db):
    col_src = db[COL_PRODUTOS]
    col_dst = db[COL_PRODUTOS_VECTOR]
    n       = VECTOR_SAMPLE_SIZE
    print(f"\n  ▶ {COL_PRODUTOS_VECTOR} via $sample — {n:,} docs\n")
    start_ts = time.time()

    # Server-side copy ($sample → $merge): the subset never travels to the
    # client (500K docs × ~2 KB used to be loaded into Python memory).
    pipeline = [{"$sample": {"size": n}}, {"$project": {"_id": 0}},
                {"$merge": {"into": COL_PRODUTOS_VECTOR, "whenMatched": "keepExisting",
                            "whenNotMatched": "insert"}}]
    list(col_src.aggregate(pipeline, allowDiskUse=True))
    total = col_dst.count_documents({})

    elapsed = time.time() - start_ts
    print(f"\n  ✅ {COL_PRODUTOS_VECTOR} — {total:,} em {elapsed:.1f}s\n")

def populate_avaliacoes(db, produto_ids: list):
    col      = db[COL_AVALIACOES]
    total    = TOTAL_DOCS_AVALIACOES
    inserted, start_ts = 0, time.time()
    print(f"\n  ▶ {COL_AVALIACOES} — {total:,} docs\n")

    while inserted < total:
        n     = min(BATCH_SIZE, total - inserted)
        # pick a product PER REVIEW (a single choice per 10k-batch would
        # concentrate every review on ~10 products)
        batch = []
        for _ in range(n):
            pid, cat = random.choice(produto_ids) if produto_ids else (str(uuid.uuid4()), "Eletrônicos")
            batch.append(make_avaliacao(pid, cat))
        insert_bulk(col, batch)
        inserted += n
        progress_bar(inserted, total, start_ts, label=COL_AVALIACOES)

    elapsed = time.time() - start_ts
    print(f"\n  ✅ {COL_AVALIACOES} — {inserted:,} em {elapsed:.1f}s\n")

def create_indexes(db):
    print("\n  ▶ Creating regular indexes...")

    p = db[COL_PRODUTOS]
    p.create_index([("categoria", ASCENDING)])
    p.create_index([("subcategoria", ASCENDING)])
    p.create_index([("marca", ASCENDING)])
    p.create_index([("preco", ASCENDING)])
    p.create_index([("avaliacao_media", DESCENDING)])
    p.create_index([("em_estoque", ASCENDING)])
    p.create_index([("categoria", ASCENDING), ("preco", ASCENDING)])
    p.create_index([("marca", ASCENDING), ("avaliacao_media", DESCENDING)])
    p.create_index([("categoria", ASCENDING), ("avaliacao_media", DESCENDING), ("preco", ASCENDING)])
    print(f"    ✅ {COL_PRODUTOS} — 9 indexes")

    pv = db[COL_PRODUTOS_VECTOR]
    pv.create_index([("categoria", ASCENDING)])
    pv.create_index([("preco", ASCENDING)])
    print(f"    ✅ {COL_PRODUTOS_VECTOR} — 2 indexes")

    av = db[COL_AVALIACOES]
    av.create_index([("produto_id", ASCENDING)])
    av.create_index([("categoria", ASCENDING)])
    av.create_index([("nota", ASCENDING)])
    av.create_index([("data", DESCENDING)])
    print(f"    ✅ {COL_AVALIACOES} — 4 indexes\n")

# ══════════════════════════════════════════════════════════════════════
# ATLAS UI INSTRUCTIONS
# ══════════════════════════════════════════════════════════════════════

def print_atlas_instructions():
    print(f"""
╔══════════════════════════════════════════════════════════════════════════╗
║   NEXT STEPS — Atlas UI                                                  ║
╚══════════════════════════════════════════════════════════════════════════╝

Database: {DB_NAME}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1.  ATLAS SEARCH — collection: {COL_PRODUTOS}
    UI → Atlas Search → Create Search Index → JSON Editor
    Name: produtos_search

    NOTE the multi-type fields: `token` on categoria and `number` on preco let
    the app run filters INSIDE $search (compound.filter) so the total count
    reflects the filters — the *Facet types alone only support faceting.

{{
  "mappings": {{
    "dynamic": false,
    "fields": {{
      "nome": [
        {{
          "type": "autocomplete",
          "analyzer": "lucene.standard",
          "tokenization": "edgeGram",
          "minGrams": 2,
          "maxGrams": 15
        }},
        {{ "type": "string", "analyzer": "lucene.standard" }}
      ],
      "descricao":   {{ "type": "string", "analyzer": "lucene.portuguese" }},
      "marca":       {{ "type": "string" }},
      "produto_id":  {{ "type": "token" }},
      "categoria":   [
        {{ "type": "stringFacet" }},
        {{ "type": "token" }}
      ],
      "subcategoria":{{ "type": "stringFacet" }},
      "genero":      {{ "type": "stringFacet" }},
      "em_estoque":  {{ "type": "boolean" }},
      "preco":       [
        {{ "type": "numberFacet" }},
        {{ "type": "number" }}
      ],
      "avaliacao_media": {{ "type": "number" }}
    }}
  }},
  "synonyms": [
    {{
      "name": "sinonimos_produtos",
      "analyzer": "lucene.standard",
      "source": {{ "collection": "sinonimos" }}
    }}
  ]
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2.  VECTOR SEARCH — collection: {COL_PRODUTOS_VECTOR}
    UI → Atlas Search → Create Search Index → JSON Editor
    Name: produtos_vector

{{
  "fields": [
    {{
      "type": "vector",
      "path": "descricao",
      "numDimensions": 1024,
      "similarity": "cosine",
      "autoEmbedding": {{
        "model": {{
          "provider": "voyageAI",
          "name": "voyage-4"
        }}
      }}
    }},
    {{
      "type": "filter",
      "path": "categoria"
    }},
    {{
      "type": "filter",
      "path": "preco"
    }},
    {{
      "type": "filter",
      "path": "em_estoque"
    }}
  ]
}}

  ⚠️  Requires Voyage AI integrated with Atlas (Atlas UI → Integrations)
  ⚠️  Vector index build: ~30-50 min for 500K docs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2b. ATLAS SEARCH on the VECTOR collection — collection: {COL_PRODUTOS_VECTOR}
    UI → Atlas Search → Create Search Index → JSON Editor
    Name: produtos_vector_search

    WHY: native hybrid search ($rankFusion, MongoDB 8.1+) runs both
    sub-pipelines against ONE collection, so the lexical index must live on
    the same collection as the vector index. This also makes the RRF
    comparisons methodologically honest (same corpus on both sides).
    The app detects this index automatically — without it, the hybrid tabs
    fall back to application-side RRF over the two collections.

{{
  "mappings": {{
    "dynamic": false,
    "fields": {{
      "nome": [
        {{
          "type": "autocomplete",
          "analyzer": "lucene.standard",
          "tokenization": "edgeGram",
          "minGrams": 2,
          "maxGrams": 15
        }},
        {{ "type": "string", "analyzer": "lucene.standard" }}
      ],
      "descricao":   {{ "type": "string", "analyzer": "lucene.portuguese" }},
      "marca":       {{ "type": "string" }},
      "produto_id":  {{ "type": "token" }},
      "categoria":   {{ "type": "token" }},
      "em_estoque":  {{ "type": "boolean" }},
      "preco":       {{ "type": "number" }}
    }}
  }}
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3.  SYNONYMS (optional — improves fuzzy search)
    Create the collection: sinonimos
    Insert the documents below (Portuguese synonyms):

{{ "mappingType": "equivalent", "synonyms": ["notebook", "computador", "laptop", "máquina"] }}
{{ "mappingType": "equivalent", "synonyms": ["fone", "headphone", "headset", "auricular", "earphone"] }}
{{ "mappingType": "equivalent", "synonyms": ["tênis", "calçado", "sapatênis", "sneaker"] }}
{{ "mappingType": "equivalent", "synonyms": ["academia", "musculação", "fitness", "treino", "gym"] }}
{{ "mappingType": "equivalent", "synonyms": ["perfume", "fragrância", "cologne", "eau de toilette"] }}
{{ "mappingType": "equivalent", "synonyms": ["celular", "smartphone", "aparelho", "telefone"] }}
{{ "mappingType": "equivalent", "synonyms": ["tv", "televisão", "televisor", "smart tv"] }}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

4.  READY-TO-USE DEMO QUERIES (in Portuguese, typed into the UI)

  - Lexical vs semantic gap (the standout moment of the demo):
    Query: "academia em casa"
    Atlas Search → 0 results (the words do not appear in the docs)
    Vector Search → halteres, colchonete, whey, kettlebell, elástico

    Query: "presente para o dia dos pais"
    Atlas Search → 0 results
    Vector Search → perfumes, relógios, ração premium, livros de negócios

    Query: "proteção solar para o rosto"
    Atlas Search → 0 results
    Vector Search → protetor solar, hidratante com FPS, base com proteção

  - Fuzzy (typo tolerance):
    "samsumg" → Samsung | "adidass" → Adidas | "notebokk" → notebook

  - Agent (end-to-end):
    "Me recomende um notebook para programação até R$ 3.000"
    "Compare os melhores smartphones Samsung vs Apple"
    "Quais são os produtos mais bem avaliados na categoria Esportes?"
    "Encontre tênis de corrida feminino até R$ 500 com nota acima de 4"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

5️⃣  .env

    MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/
    DB_NAME={DB_NAME}
    ANTHROPIC_API_KEY=sk-ant-...

══════════════════════════════════════════════════════════════════════════
""")

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 72)
    print("  MongoDB Atlas POC — Marketplace Seeder")
    print("=" * 72)
    print(f"  Database  : {DB_NAME}")
    print(f"  produtos  : {TOTAL_DOCS_PRODUTOS:>14,} docs")
    print(f"  vector    : {VECTOR_SAMPLE_SIZE:>14,} docs")
    print(f"  avaliacoes: {TOTAL_DOCS_AVALIACOES:>14,} docs")
    print(f"  batch     : {BATCH_SIZE:>14,}")
    print(f"  drop_first: {DROP_BEFORE_POPULATE}")

    assert_writable_db(DB_NAME)
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    db     = client[DB_NAME]

    if DROP_BEFORE_POPULATE:
        print("\n  ⚠  Dropping existing collections...")
        for c in [COL_PRODUTOS, COL_PRODUTOS_VECTOR, COL_AVALIACOES]:
            db[c].drop()
        print("  ✅ Drop complete")

    t0 = time.time()

    produto_ids = populate_produtos(db, TOTAL_DOCS_PRODUTOS)
    populate_vector_sample(db)
    populate_avaliacoes(db, produto_ids)
    create_indexes(db)

    total_docs = TOTAL_DOCS_PRODUTOS + VECTOR_SAMPLE_SIZE + TOTAL_DOCS_AVALIACOES
    elapsed    = time.time() - t0

    print(f"\n{'='*72}")
    print(f"  ✅  {total_docs:,} docs inserted in {elapsed/60:.1f} min")
    print(f"{'='*72}")

    print_atlas_instructions()
    client.close()
