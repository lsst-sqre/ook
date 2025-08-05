--
-- PostgreSQL database dump
--

-- Dumped from database version 16.9 (Debian 16.9-1.pgdg120+1)
-- Dumped by pg_dump version 16.9 (Debian 16.9-1.pgdg120+1)

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

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner:
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: affiliation; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.affiliation (
    id bigint NOT NULL,
    internal_id text NOT NULL,
    name text NOT NULL,
    department text,
    email_domain text,
    ror_id text,
    address_street text,
    address_city text,
    address_state text,
    address_postal_code text,
    address_country text,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.affiliation OWNER TO test;

--
-- Name: affiliation_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.affiliation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.affiliation_id_seq OWNER TO test;

--
-- Name: affiliation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.affiliation_id_seq OWNED BY public.affiliation.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO test;

--
-- Name: author; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.author (
    id bigint NOT NULL,
    internal_id text NOT NULL,
    surname text NOT NULL,
    given_name text,
    notes text[] NOT NULL,
    email text,
    orcid text,
    date_updated timestamp with time zone NOT NULL,
    search_vector text GENERATED ALWAYS AS ((((COALESCE((given_name || ' '::text), ''::text) || surname) || ' '::text) || COALESCE(((surname || ', '::text) || given_name), ''::text))) STORED NOT NULL
);


ALTER TABLE public.author OWNER TO test;

--
-- Name: author_affiliations; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.author_affiliations (
    author_id bigint NOT NULL,
    affiliation_id bigint NOT NULL,
    "position" integer NOT NULL
);


ALTER TABLE public.author_affiliations OWNER TO test;

--
-- Name: author_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.author_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.author_id_seq OWNER TO test;

--
-- Name: author_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.author_id_seq OWNED BY public.author.id;


--
-- Name: contributor; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.contributor (
    id bigint NOT NULL,
    resource_id bigint NOT NULL,
    "order" integer NOT NULL,
    role text NOT NULL,
    author_id bigint
);


ALTER TABLE public.contributor OWNER TO test;

--
-- Name: contributor_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.contributor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.contributor_id_seq OWNER TO test;

--
-- Name: contributor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.contributor_id_seq OWNED BY public.contributor.id;


--
-- Name: document_resource; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.document_resource (
    id bigint NOT NULL,
    series text NOT NULL,
    handle text NOT NULL,
    generator text,
    number integer NOT NULL
);


ALTER TABLE public.document_resource OWNER TO test;

--
-- Name: external_reference; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.external_reference (
    id bigint NOT NULL,
    url text,
    doi text,
    arxiv_id text,
    isbn text,
    issn text,
    ads_bibcode text,
    type text,
    title text,
    publication_year text,
    volume text,
    issue text,
    number text,
    number_type text,
    first_page text,
    last_page text,
    publisher text,
    edition text,
    contributors json
);


ALTER TABLE public.external_reference OWNER TO test;

--
-- Name: external_reference_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.external_reference_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.external_reference_id_seq OWNER TO test;

--
-- Name: external_reference_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.external_reference_id_seq OWNED BY public.external_reference.id;


--
-- Name: link; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.link (
    id bigint NOT NULL,
    type character varying NOT NULL,
    html_url text NOT NULL,
    source_type text NOT NULL,
    source_title text NOT NULL,
    source_collection_title text,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.link OWNER TO test;

--
-- Name: link_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.link_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.link_id_seq OWNER TO test;

--
-- Name: link_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.link_id_seq OWNED BY public.link.id;


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
-- Name: resource; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.resource (
    id bigint NOT NULL,
    resource_class character varying,
    date_created timestamp with time zone NOT NULL,
    date_updated timestamp with time zone NOT NULL,
    title text NOT NULL,
    description text,
    url text,
    doi text,
    date_resource_published timestamp with time zone,
    date_resource_updated timestamp with time zone,
    version character varying,
    type character varying
);


ALTER TABLE public.resource OWNER TO test;

--
-- Name: resource_relation; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.resource_relation (
    id bigint NOT NULL,
    source_resource_id bigint NOT NULL,
    related_resource_id bigint,
    related_external_ref_id bigint,
    relation_type text NOT NULL,
    CONSTRAINT chk_exactly_one_related CHECK ((((related_resource_id IS NOT NULL) AND (related_external_ref_id IS NULL)) OR ((related_resource_id IS NULL) AND (related_external_ref_id IS NOT NULL))))
);


ALTER TABLE public.resource_relation OWNER TO test;

--
-- Name: resource_relation_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.resource_relation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.resource_relation_id_seq OWNER TO test;

--
-- Name: resource_relation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.resource_relation_id_seq OWNED BY public.resource_relation.id;


--
-- Name: sdm_column; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.sdm_column (
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


ALTER TABLE public.sdm_column OWNER TO test;

--
-- Name: sdm_column_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.sdm_column_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sdm_column_id_seq OWNER TO test;

--
-- Name: sdm_column_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.sdm_column_id_seq OWNED BY public.sdm_column.id;


--
-- Name: sdm_schema; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.sdm_schema (
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


ALTER TABLE public.sdm_schema OWNER TO test;

--
-- Name: sdm_schema_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.sdm_schema_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sdm_schema_id_seq OWNER TO test;

--
-- Name: sdm_schema_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.sdm_schema_id_seq OWNED BY public.sdm_schema.id;


--
-- Name: sdm_table; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.sdm_table (
    id bigint NOT NULL,
    schema_id bigint NOT NULL,
    name text NOT NULL,
    felis_id text NOT NULL,
    description text,
    tap_table_index bigint,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.sdm_table OWNER TO test;

--
-- Name: sdm_table_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.sdm_table_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sdm_table_id_seq OWNER TO test;

--
-- Name: sdm_table_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.sdm_table_id_seq OWNED BY public.sdm_table.id;


--
-- Name: term; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.term (
    id integer NOT NULL,
    term text NOT NULL,
    definition text NOT NULL,
    definition_es text,
    is_abbr boolean NOT NULL,
    contexts text[] NOT NULL,
    related_documentation text[] NOT NULL,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.term OWNER TO test;

--
-- Name: term_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.term_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.term_id_seq OWNER TO test;

--
-- Name: term_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.term_id_seq OWNED BY public.term.id;


--
-- Name: term_relationships; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.term_relationships (
    source_term_id integer NOT NULL,
    related_term_id integer NOT NULL
);


ALTER TABLE public.term_relationships OWNER TO test;

--
-- Name: affiliation id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.affiliation ALTER COLUMN id SET DEFAULT nextval('public.affiliation_id_seq'::regclass);


--
-- Name: author id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author ALTER COLUMN id SET DEFAULT nextval('public.author_id_seq'::regclass);


--
-- Name: contributor id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.contributor ALTER COLUMN id SET DEFAULT nextval('public.contributor_id_seq'::regclass);


--
-- Name: external_reference id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference ALTER COLUMN id SET DEFAULT nextval('public.external_reference_id_seq'::regclass);


--
-- Name: link id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.link ALTER COLUMN id SET DEFAULT nextval('public.link_id_seq'::regclass);


--
-- Name: resource_relation id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource_relation ALTER COLUMN id SET DEFAULT nextval('public.resource_relation_id_seq'::regclass);


--
-- Name: sdm_column id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_column ALTER COLUMN id SET DEFAULT nextval('public.sdm_column_id_seq'::regclass);


--
-- Name: sdm_schema id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_schema ALTER COLUMN id SET DEFAULT nextval('public.sdm_schema_id_seq'::regclass);


--
-- Name: sdm_table id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_table ALTER COLUMN id SET DEFAULT nextval('public.sdm_table_id_seq'::regclass);


--
-- Name: term id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term ALTER COLUMN id SET DEFAULT nextval('public.term_id_seq'::regclass);


--
-- Data for Name: affiliation; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.affiliation (id, internal_id, name, department, email_domain, ror_id, address_street, address_city, address_state, address_postal_code, address_country, date_updated) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.alembic_version (version_num) FROM stdin;
c03d146610d8
\.


--
-- Data for Name: author; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.author (id, internal_id, surname, given_name, notes, email, orcid, date_updated) FROM stdin;
\.


--
-- Data for Name: author_affiliations; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.author_affiliations (author_id, affiliation_id, "position") FROM stdin;
\.


--
-- Data for Name: contributor; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.contributor (id, resource_id, "order", role, author_id) FROM stdin;
\.


--
-- Data for Name: document_resource; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.document_resource (id, series, handle, generator, number) FROM stdin;
\.


--
-- Data for Name: external_reference; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.external_reference (id, url, doi, arxiv_id, isbn, issn, ads_bibcode, type, title, publication_year, volume, issue, number, number_type, first_page, last_page, publisher, edition, contributors) FROM stdin;
\.


--
-- Data for Name: link; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.link (id, type, html_url, source_type, source_title, source_collection_title, date_updated) FROM stdin;
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
-- Data for Name: resource; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.resource (id, resource_class, date_created, date_updated, title, description, url, doi, date_resource_published, date_resource_updated, version, type) FROM stdin;
\.


--
-- Data for Name: resource_relation; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.resource_relation (id, source_resource_id, related_resource_id, related_external_ref_id, relation_type) FROM stdin;
\.


--
-- Data for Name: sdm_column; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.sdm_column (id, table_id, name, felis_id, description, datatype, ivoa_ucd, ivoa_unit, tap_column_index, date_updated) FROM stdin;
\.


--
-- Data for Name: sdm_schema; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.sdm_schema (id, name, felis_id, description, github_owner, github_repo, github_ref, github_path, date_updated) FROM stdin;
\.


--
-- Data for Name: sdm_table; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.sdm_table (id, schema_id, name, felis_id, description, tap_table_index, date_updated) FROM stdin;
\.


--
-- Data for Name: term; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.term (id, term, definition, definition_es, is_abbr, contexts, related_documentation, date_updated) FROM stdin;
\.


--
-- Data for Name: term_relationships; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.term_relationships (source_term_id, related_term_id) FROM stdin;
\.


--
-- Name: affiliation_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.affiliation_id_seq', 1, false);


--
-- Name: author_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.author_id_seq', 1, false);


--
-- Name: contributor_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.contributor_id_seq', 1, false);


--
-- Name: external_reference_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.external_reference_id_seq', 1, false);


--
-- Name: link_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.link_id_seq', 1, false);


--
-- Name: resource_relation_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.resource_relation_id_seq', 1, false);


--
-- Name: sdm_column_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.sdm_column_id_seq', 1, false);


--
-- Name: sdm_schema_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.sdm_schema_id_seq', 1, false);


--
-- Name: sdm_table_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.sdm_table_id_seq', 1, false);


--
-- Name: term_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.term_id_seq', 1, false);


--
-- Name: affiliation affiliation_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.affiliation
    ADD CONSTRAINT affiliation_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: author_affiliations author_affiliations_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author_affiliations
    ADD CONSTRAINT author_affiliations_pkey PRIMARY KEY (author_id, affiliation_id);


--
-- Name: author author_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author
    ADD CONSTRAINT author_pkey PRIMARY KEY (id);


--
-- Name: contributor contributor_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.contributor
    ADD CONSTRAINT contributor_pkey PRIMARY KEY (id);


--
-- Name: document_resource document_resource_handle_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.document_resource
    ADD CONSTRAINT document_resource_handle_key UNIQUE (handle);


--
-- Name: document_resource document_resource_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.document_resource
    ADD CONSTRAINT document_resource_pkey PRIMARY KEY (id);


--
-- Name: external_reference external_reference_ads_bibcode_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference
    ADD CONSTRAINT external_reference_ads_bibcode_key UNIQUE (ads_bibcode);


--
-- Name: external_reference external_reference_arxiv_id_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference
    ADD CONSTRAINT external_reference_arxiv_id_key UNIQUE (arxiv_id);


--
-- Name: external_reference external_reference_doi_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference
    ADD CONSTRAINT external_reference_doi_key UNIQUE (doi);


--
-- Name: external_reference external_reference_isbn_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference
    ADD CONSTRAINT external_reference_isbn_key UNIQUE (isbn);


--
-- Name: external_reference external_reference_issn_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference
    ADD CONSTRAINT external_reference_issn_key UNIQUE (issn);


--
-- Name: external_reference external_reference_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.external_reference
    ADD CONSTRAINT external_reference_pkey PRIMARY KEY (id);


--
-- Name: link link_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.link
    ADD CONSTRAINT link_pkey PRIMARY KEY (id);


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
-- Name: resource resource_doi_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource
    ADD CONSTRAINT resource_doi_key UNIQUE (doi);


--
-- Name: resource resource_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource
    ADD CONSTRAINT resource_pkey PRIMARY KEY (id);


--
-- Name: resource_relation resource_relation_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource_relation
    ADD CONSTRAINT resource_relation_pkey PRIMARY KEY (id);


--
-- Name: sdm_column sdm_column_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_column
    ADD CONSTRAINT sdm_column_pkey PRIMARY KEY (id);


--
-- Name: sdm_schema sdm_schema_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_schema
    ADD CONSTRAINT sdm_schema_pkey PRIMARY KEY (id);


--
-- Name: sdm_table sdm_table_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_table
    ADD CONSTRAINT sdm_table_pkey PRIMARY KEY (id);


--
-- Name: term term_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term
    ADD CONSTRAINT term_pkey PRIMARY KEY (id);


--
-- Name: term_relationships term_relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term_relationships
    ADD CONSTRAINT term_relationships_pkey PRIMARY KEY (source_term_id, related_term_id);


--
-- Name: affiliation uq_affiliation_internal_id_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.affiliation
    ADD CONSTRAINT uq_affiliation_internal_id_name UNIQUE (internal_id, name);


--
-- Name: author uq_author_orcid; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author
    ADD CONSTRAINT uq_author_orcid UNIQUE (orcid);


--
-- Name: contributor uq_contributor_resource_order_role; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.contributor
    ADD CONSTRAINT uq_contributor_resource_order_role UNIQUE (resource_id, "order", role);


--
-- Name: document_resource uq_document_series_number; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.document_resource
    ADD CONSTRAINT uq_document_series_number UNIQUE (series, number);


--
-- Name: resource_relation uq_resource_relation; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource_relation
    ADD CONSTRAINT uq_resource_relation UNIQUE (source_resource_id, related_resource_id, related_external_ref_id, relation_type);


--
-- Name: sdm_column uq_sdm_column_table_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_column
    ADD CONSTRAINT uq_sdm_column_table_name UNIQUE (table_id, name);


--
-- Name: sdm_schema uq_sdm_schema_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_schema
    ADD CONSTRAINT uq_sdm_schema_name UNIQUE (name);


--
-- Name: sdm_table uq_sdm_table_schema_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_table
    ADD CONSTRAINT uq_sdm_table_schema_name UNIQUE (schema_id, name);


--
-- Name: term uq_term_definition; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term
    ADD CONSTRAINT uq_term_definition UNIQUE (term, definition);


--
-- Name: idx_author_given_name_trgm; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_author_given_name_trgm ON public.author USING gin (given_name public.gin_trgm_ops);


--
-- Name: idx_author_search_vector_trgm; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_author_search_vector_trgm ON public.author USING gin (search_vector public.gin_trgm_ops);


--
-- Name: idx_author_surname_trgm; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_author_surname_trgm ON public.author USING gin (surname public.gin_trgm_ops);


--
-- Name: idx_document_series_number; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_document_series_number ON public.document_resource USING btree (series, number);


--
-- Name: idx_resource_class; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_resource_class ON public.resource USING btree (resource_class);


--
-- Name: idx_resource_date_published; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_resource_date_published ON public.resource USING btree (date_resource_published);


--
-- Name: idx_resource_date_updated; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_resource_date_updated ON public.resource USING btree (date_resource_updated);


--
-- Name: idx_resource_relation_source; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_resource_relation_source ON public.resource_relation USING btree (source_resource_id);


--
-- Name: idx_resource_relation_type; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX idx_resource_relation_type ON public.resource_relation USING btree (relation_type);


--
-- Name: ix_affiliation_internal_id; Type: INDEX; Schema: public; Owner: test
--

CREATE UNIQUE INDEX ix_affiliation_internal_id ON public.affiliation USING btree (internal_id);


--
-- Name: ix_affiliation_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_affiliation_name ON public.affiliation USING btree (name);


--
-- Name: ix_author_given_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_author_given_name ON public.author USING btree (given_name);


--
-- Name: ix_author_internal_id; Type: INDEX; Schema: public; Owner: test
--

CREATE UNIQUE INDEX ix_author_internal_id ON public.author USING btree (internal_id);


--
-- Name: ix_author_surname; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_author_surname ON public.author USING btree (surname);


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
-- Name: ix_sdm_column_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_column_name ON public.sdm_column USING btree (name);


--
-- Name: ix_sdm_column_table_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_column_table_id ON public.sdm_column USING btree (table_id);


--
-- Name: ix_sdm_schema_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_schema_name ON public.sdm_schema USING btree (name);


--
-- Name: ix_sdm_table_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_table_name ON public.sdm_table USING btree (name);


--
-- Name: ix_sdm_table_schema_id; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_sdm_table_schema_id ON public.sdm_table USING btree (schema_id);


--
-- Name: ix_term_contexts; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_term_contexts ON public.term USING btree (contexts);


--
-- Name: ix_term_definition; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_term_definition ON public.term USING btree (definition);


--
-- Name: ix_term_term; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_term_term ON public.term USING btree (term);


--
-- Name: author_affiliations author_affiliations_affiliation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author_affiliations
    ADD CONSTRAINT author_affiliations_affiliation_id_fkey FOREIGN KEY (affiliation_id) REFERENCES public.affiliation(id);


--
-- Name: author_affiliations author_affiliations_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author_affiliations
    ADD CONSTRAINT author_affiliations_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.author(id);


--
-- Name: contributor contributor_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.contributor
    ADD CONSTRAINT contributor_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.author(id);


--
-- Name: contributor contributor_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.contributor
    ADD CONSTRAINT contributor_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resource(id);


--
-- Name: document_resource document_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.document_resource
    ADD CONSTRAINT document_resource_id_fkey FOREIGN KEY (id) REFERENCES public.resource(id);


--
-- Name: links_sdm_columns links_sdm_columns_column_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_columns
    ADD CONSTRAINT links_sdm_columns_column_id_fkey FOREIGN KEY (column_id) REFERENCES public.sdm_column(id);


--
-- Name: links_sdm_columns links_sdm_columns_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_columns
    ADD CONSTRAINT links_sdm_columns_id_fkey FOREIGN KEY (id) REFERENCES public.link(id);


--
-- Name: links_sdm_schemas links_sdm_schemas_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_schemas
    ADD CONSTRAINT links_sdm_schemas_id_fkey FOREIGN KEY (id) REFERENCES public.link(id);


--
-- Name: links_sdm_schemas links_sdm_schemas_schema_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_schemas
    ADD CONSTRAINT links_sdm_schemas_schema_id_fkey FOREIGN KEY (schema_id) REFERENCES public.sdm_schema(id);


--
-- Name: links_sdm_tables links_sdm_tables_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_tables
    ADD CONSTRAINT links_sdm_tables_id_fkey FOREIGN KEY (id) REFERENCES public.link(id);


--
-- Name: links_sdm_tables links_sdm_tables_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.links_sdm_tables
    ADD CONSTRAINT links_sdm_tables_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.sdm_table(id);


--
-- Name: resource_relation resource_relation_related_external_ref_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource_relation
    ADD CONSTRAINT resource_relation_related_external_ref_id_fkey FOREIGN KEY (related_external_ref_id) REFERENCES public.external_reference(id);


--
-- Name: resource_relation resource_relation_related_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource_relation
    ADD CONSTRAINT resource_relation_related_resource_id_fkey FOREIGN KEY (related_resource_id) REFERENCES public.resource(id);


--
-- Name: resource_relation resource_relation_source_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.resource_relation
    ADD CONSTRAINT resource_relation_source_resource_id_fkey FOREIGN KEY (source_resource_id) REFERENCES public.resource(id);


--
-- Name: sdm_column sdm_column_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_column
    ADD CONSTRAINT sdm_column_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.sdm_table(id);


--
-- Name: sdm_table sdm_table_schema_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.sdm_table
    ADD CONSTRAINT sdm_table_schema_id_fkey FOREIGN KEY (schema_id) REFERENCES public.sdm_schema(id);


--
-- Name: term_relationships term_relationships_related_term_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term_relationships
    ADD CONSTRAINT term_relationships_related_term_id_fkey FOREIGN KEY (related_term_id) REFERENCES public.term(id) ON DELETE CASCADE;


--
-- Name: term_relationships term_relationships_source_term_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term_relationships
    ADD CONSTRAINT term_relationships_source_term_id_fkey FOREIGN KEY (source_term_id) REFERENCES public.term(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

