--
-- PostgreSQL database dump
--

-- Dumped from database version 16.8 (Debian 16.8-1.pgdg120+1)
-- Dumped by pg_dump version 16.8 (Debian 16.8-1.pgdg120+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO test;

--
-- Name: links; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.links (
    id bigint NOT NULL,
    type character varying NOT NULL,
    html_url text NOT NULL,
    source_type text NOT NULL,
    source_title text NOT NULL,
    source_collection_title text,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.links OWNER TO test;

--
-- Name: links_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.links_id_seq OWNER TO test;

--
-- Name: links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.links_id_seq OWNED BY public.links.id;


--
-- Name: links_sdm_columns; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.links_sdm_columns (
    id bigint NOT NULL,
    column_id bigint NOT NULL
);


ALTER TABLE public.links_sdm_columns OWNER TO test;

--
-- Name: links_sdm_schemas; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.links_sdm_schemas (
    id bigint NOT NULL,
    schema_id bigint NOT NULL
);


ALTER TABLE public.links_sdm_schemas OWNER TO test;

--
-- Name: links_sdm_tables; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.links_sdm_tables (
    id bigint NOT NULL,
    table_id bigint NOT NULL
);


ALTER TABLE public.links_sdm_tables OWNER TO test;

--
-- Name: sdm_columns; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.sdm_columns (
    id bigint NOT NULL,
    table_id bigint NOT NULL,
    name text NOT NULL,
    felis_id text NOT NULL,
    description text,
    datatype text NOT NULL,
    ivoa_ucd text,
    ivoa_unit text,
    tap_column_index bigint,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.sdm_columns OWNER TO test;

--
-- Name: sdm_columns_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.sdm_columns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sdm_columns_id_seq OWNER TO test;

--
-- Name: sdm_columns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.sdm_columns_id_seq OWNED BY public.sdm_columns.id;


--
-- Name: sdm_schemas; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.sdm_schemas (
    id bigint NOT NULL,
    name text NOT NULL,
    felis_id text NOT NULL,
    description text,
    github_owner text NOT NULL,
    github_repo text NOT NULL,
    github_ref text NOT NULL,
    github_path text NOT NULL,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.sdm_schemas OWNER TO test;

--
-- Name: sdm_schemas_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.sdm_schemas_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sdm_schemas_id_seq OWNER TO test;

--
-- Name: sdm_schemas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.sdm_schemas_id_seq OWNED BY public.sdm_schemas.id;


--
-- Name: sdm_tables; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.sdm_tables (
    id bigint NOT NULL,
    schema_id bigint NOT NULL,
    name text NOT NULL,
    felis_id text NOT NULL,
    description text,
    tap_table_index bigint,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.sdm_tables OWNER TO test;

--
-- Name: sdm_tables_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.sdm_tables_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sdm_tables_id_seq OWNER TO test;

--
-- Name: sdm_tables_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.sdm_tables_id_seq OWNED BY public.sdm_tables.id;


--
-- Name: links id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links ALTER COLUMN id SET DEFAULT nextval('public.links_id_seq'::regclass);


--
-- Name: sdm_columns id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_columns ALTER COLUMN id SET DEFAULT nextval('public.sdm_columns_id_seq'::regclass);


--
-- Name: sdm_schemas id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_schemas ALTER COLUMN id SET DEFAULT nextval('public.sdm_schemas_id_seq'::regclass);


--
-- Name: sdm_tables id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_tables ALTER COLUMN id SET DEFAULT nextval('public.sdm_tables_id_seq'::regclass);


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.alembic_version (version_num) FROM stdin;
82b60f520823
\.


--
-- Data for Name: links; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.links (id, type, html_url, source_type, source_title, source_collection_title, date_updated) FROM stdin;
\.


--
-- Data for Name: links_sdm_columns; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.links_sdm_columns (id, column_id) FROM stdin;
\.


--
-- Data for Name: links_sdm_schemas; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.links_sdm_schemas (id, schema_id) FROM stdin;
\.


--
-- Data for Name: links_sdm_tables; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.links_sdm_tables (id, table_id) FROM stdin;
\.


--
-- Data for Name: sdm_columns; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.sdm_columns (id, table_id, name, felis_id, description, datatype, ivoa_ucd, ivoa_unit, tap_column_index, date_updated) FROM stdin;
\.


--
-- Data for Name: sdm_schemas; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.sdm_schemas (id, name, felis_id, description, github_owner, github_repo, github_ref, github_path, date_updated) FROM stdin;
\.


--
-- Data for Name: sdm_tables; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.sdm_tables (id, schema_id, name, felis_id, description, tap_table_index, date_updated) FROM stdin;
\.


--
-- Name: links_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.links_id_seq', 1, false);


--
-- Name: sdm_columns_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.sdm_columns_id_seq', 1, false);


--
-- Name: sdm_schemas_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.sdm_schemas_id_seq', 1, false);


--
-- Name: sdm_tables_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.sdm_tables_id_seq', 1, false);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: links links_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links
    ADD CONSTRAINT links_pkey PRIMARY KEY (id);


--
-- Name: links_sdm_columns links_sdm_columns_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_columns
    ADD CONSTRAINT links_sdm_columns_pkey PRIMARY KEY (id);


--
-- Name: links_sdm_schemas links_sdm_schemas_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_schemas
    ADD CONSTRAINT links_sdm_schemas_pkey PRIMARY KEY (id);


--
-- Name: links_sdm_tables links_sdm_tables_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_tables
    ADD CONSTRAINT links_sdm_tables_pkey PRIMARY KEY (id);


--
-- Name: sdm_columns sdm_columns_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_columns
    ADD CONSTRAINT sdm_columns_pkey PRIMARY KEY (id);


--
-- Name: sdm_schemas sdm_schemas_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_schemas
    ADD CONSTRAINT sdm_schemas_pkey PRIMARY KEY (id);


--
-- Name: sdm_tables sdm_tables_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_tables
    ADD CONSTRAINT sdm_tables_pkey PRIMARY KEY (id);


--
-- Name: sdm_columns uq_sdm_column_table_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_columns
    ADD CONSTRAINT uq_sdm_column_table_name UNIQUE (table_id, name);


--
-- Name: sdm_schemas uq_sdm_schema_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_schemas
    ADD CONSTRAINT uq_sdm_schema_name UNIQUE (name);


--
-- Name: sdm_tables uq_sdm_table_schema_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_tables
    ADD CONSTRAINT uq_sdm_table_schema_name UNIQUE (schema_id, name);


--
-- Name: ix_links_sdm_columns_column_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_links_sdm_columns_column_id ON public.links_sdm_columns USING btree (column_id);


--
-- Name: ix_links_sdm_schemas_schema_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_links_sdm_schemas_schema_id ON public.links_sdm_schemas USING btree (schema_id);


--
-- Name: ix_links_sdm_tables_table_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_links_sdm_tables_table_id ON public.links_sdm_tables USING btree (table_id);


--
-- Name: ix_sdm_columns_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_columns_name ON public.sdm_columns USING btree (name);


--
-- Name: ix_sdm_columns_table_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_columns_table_id ON public.sdm_columns USING btree (table_id);


--
-- Name: ix_sdm_schemas_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_schemas_name ON public.sdm_schemas USING btree (name);


--
-- Name: ix_sdm_tables_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_tables_name ON public.sdm_tables USING btree (name);


--
-- Name: ix_sdm_tables_schema_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_tables_schema_id ON public.sdm_tables USING btree (schema_id);


--
-- Name: links_sdm_columns links_sdm_columns_column_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_columns
    ADD CONSTRAINT links_sdm_columns_column_id_fkey FOREIGN KEY (column_id) REFERENCES public.sdm_columns(id);


--
-- Name: links_sdm_columns links_sdm_columns_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_columns
    ADD CONSTRAINT links_sdm_columns_id_fkey FOREIGN KEY (id) REFERENCES public.links(id);


--
-- Name: links_sdm_schemas links_sdm_schemas_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_schemas
    ADD CONSTRAINT links_sdm_schemas_id_fkey FOREIGN KEY (id) REFERENCES public.links(id);


--
-- Name: links_sdm_schemas links_sdm_schemas_schema_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_schemas
    ADD CONSTRAINT links_sdm_schemas_schema_id_fkey FOREIGN KEY (schema_id) REFERENCES public.sdm_schemas(id);


--
-- Name: links_sdm_tables links_sdm_tables_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_tables
    ADD CONSTRAINT links_sdm_tables_id_fkey FOREIGN KEY (id) REFERENCES public.links(id);


--
-- Name: links_sdm_tables links_sdm_tables_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_tables
    ADD CONSTRAINT links_sdm_tables_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.sdm_tables(id);


--
-- Name: sdm_columns sdm_columns_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_columns
    ADD CONSTRAINT sdm_columns_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.sdm_tables(id);


--
-- Name: sdm_tables sdm_tables_schema_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_tables
    ADD CONSTRAINT sdm_tables_schema_id_fkey FOREIGN KEY (schema_id) REFERENCES public.sdm_schemas(id);


--
-- PostgreSQL database dump complete
--

