-- Base de dados de demo da Mercearia Atlas (dados 100% inventados).

-- Accent-insensitive product search (pão ≈ pao). The widget rewrites text filters
-- with unaccent(lower(col)) ILIKE unaccent(lower('%word%')) when this exists, and
-- falls back to plain ILIKE if the extension is missing on a real client DB.
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE categories (
    id   serial PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE products (
    id          serial PRIMARY KEY,
    category_id integer NOT NULL REFERENCES categories(id),
    sku         text NOT NULL UNIQUE,
    name        text NOT NULL,
    unit        text NOT NULL CHECK (unit IN ('un', 'kg')),
    price       numeric(10,2) NOT NULL CHECK (price >= 0),
    is_active   boolean NOT NULL DEFAULT true
);

CREATE TABLE stores (
    id      serial PRIMARY KEY,
    name    text NOT NULL,
    city    text NOT NULL,
    address text NOT NULL,
    phone   text
);

CREATE TABLE opening_hours (
    id       serial PRIMARY KEY,
    store_id integer NOT NULL REFERENCES stores(id),
    weekday  smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6), -- 0 = domingo, 6 = sábado
    opens    time NOT NULL,
    closes   time NOT NULL,
    CHECK (closes > opens),
    UNIQUE (store_id, weekday)
);

CREATE TABLE stock (
    store_id   integer NOT NULL REFERENCES stores(id),
    product_id integer NOT NULL REFERENCES products(id),
    quantity   integer NOT NULL CHECK (quantity >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, product_id)
);

CREATE TABLE customers (
    id         serial PRIMARY KEY,
    name       text NOT NULL,
    email      text NOT NULL UNIQUE,
    nif        text NOT NULL,
    city       text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Dados -------------------------------------------------------------

INSERT INTO categories (name) VALUES
    ('Mercearia'), ('Laticínios'), ('Bebidas'), ('Doces e compotas');

INSERT INTO products (category_id, sku, name, unit, price) VALUES
    (1, 'ATL-0001', 'Azeite Atlas Premium 750 ml',            'un', 8.49),
    (2, 'ATL-0002', 'Queijo da Serra da Estrela 200 g',       'un', 9.90),
    (3, 'ATL-0003', 'Café Moagem da Baixa 250 g',             'un', 4.20),
    (4, 'ATL-0004', 'Doce de abóbora caseiro Atlas 300 g',    'un', 3.75),
    (1, 'ATL-0005', 'Arroz carolino 1 kg',                    'un', 1.39),
    (1, 'ATL-0006', 'Massa esparguete 500 g',                 'un', 0.95),
    (2, 'ATL-0007', 'Leite meio-gordo 1 L',                   'un', 0.89),
    (2, 'ATL-0008', 'Iogurte natural (pack 4)',               'un', 1.79),
    (3, 'ATL-0009', 'Água mineral 1,5 L',                     'un', 0.45),
    (1, 'ATL-0010', 'Batata nova',                            'kg', 1.19),
    (1, 'ATL-0011', 'Pão de milho',                           'un', 1.20);

INSERT INTO stores (name, city, address, phone) VALUES
    ('Mercearia Atlas - Arroios', 'Lisboa',
     'Rua das Amendoeiras 48, 1170-000 Lisboa', '+351 210 000 111');

-- 0 = domingo ... 6 = sábado (igual ao PDF)
INSERT INTO opening_hours (store_id, weekday, opens, closes) VALUES
    (1, 0, '10:00', '14:00'),
    (1, 1, '08:00', '20:00'),
    (1, 2, '08:00', '20:00'),
    (1, 3, '08:00', '20:00'),
    (1, 4, '08:00', '20:00'),
    (1, 5, '08:00', '20:00'),
    (1, 6, '09:00', '19:00');

INSERT INTO stock (store_id, product_id, quantity) VALUES
    (1, 1, 24), (1, 2, 6),  (1, 3, 40), (1, 4, 12), (1, 5, 80),
    (1, 6, 65), (1, 7, 50), (1, 8, 30), (1, 9, 120), (1, 10, 0),
    (1, 11, 18);

INSERT INTO customers (name, email, nif, city) VALUES
    ('Cliente Exemplo Um',   'cliente1@example.com', '000000001', 'Lisboa'),
    ('Cliente Exemplo Dois', 'cliente2@example.com', '000000002', 'Lisboa'),
    ('Cliente Exemplo Três', 'cliente3@example.com', '000000003', 'Almada');
