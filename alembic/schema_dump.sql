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
-- Name: affiliations; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.affiliations (
    id bigint NOT NULL,
    internal_id text NOT NULL,
    name text NOT NULL,
    address text,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.affiliations OWNER TO test;

--
-- Name: affiliations_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.affiliations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.affiliations_id_seq OWNER TO test;

--
-- Name: affiliations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.affiliations_id_seq OWNED BY public.affiliations.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO test;

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
-- Name: authors; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.authors (
    id bigint NOT NULL,
    internal_id text NOT NULL,
    surname text NOT NULL,
    given_name text,
    notes text[] NOT NULL,
    email text,
    orcid text,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.authors OWNER TO test;

--
-- Name: authors_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.authors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.authors_id_seq OWNER TO test;

--
-- Name: authors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.authors_id_seq OWNED BY public.authors.id;


--
-- Name: collaborations; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.collaborations (
    id bigint NOT NULL,
    internal_id text NOT NULL,
    name text NOT NULL,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.collaborations OWNER TO test;

--
-- Name: collaborations_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.collaborations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.collaborations_id_seq OWNER TO test;

--
-- Name: collaborations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.collaborations_id_seq OWNED BY public.collaborations.id;


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
-- Name: term_relationships; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.term_relationships (
    source_term_id integer NOT NULL,
    related_term_id integer NOT NULL
);


ALTER TABLE public.term_relationships OWNER TO test;

--
-- Name: terms; Type: TABLE; Schema: public; Owner: test
--

CREATE TABLE public.terms (
    id integer NOT NULL,
    term text NOT NULL,
    definition text NOT NULL,
    definition_es text,
    is_abbr boolean NOT NULL,
    contexts text[] NOT NULL,
    related_documentation text[] NOT NULL,
    date_updated timestamp with time zone NOT NULL
);


ALTER TABLE public.terms OWNER TO test;

--
-- Name: terms_id_seq; Type: SEQUENCE; Schema: public; Owner: test
--

CREATE SEQUENCE public.terms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.terms_id_seq OWNER TO test;

--
-- Name: terms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: test
--

ALTER SEQUENCE public.terms_id_seq OWNED BY public.terms.id;


--
-- Name: affiliations id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.affiliations ALTER COLUMN id SET DEFAULT nextval('public.affiliations_id_seq'::regclass);


--
-- Name: authors id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.authors ALTER COLUMN id SET DEFAULT nextval('public.authors_id_seq'::regclass);


--
-- Name: collaborations id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.collaborations ALTER COLUMN id SET DEFAULT nextval('public.collaborations_id_seq'::regclass);


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
-- Name: terms id; Type: DEFAULT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.terms ALTER COLUMN id SET DEFAULT nextval('public.terms_id_seq'::regclass);


--
-- Data for Name: affiliations; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.affiliations (id, internal_id, name, address, date_updated) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.alembic_version (version_num) FROM stdin;
7ea34679824b
\.


--
-- Data for Name: author_affiliations; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.author_affiliations (author_id, affiliation_id, "position") FROM stdin;
\.


--
-- Data for Name: authors; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.authors (id, internal_id, surname, given_name, notes, email, orcid, date_updated) FROM stdin;
\.


--
-- Data for Name: collaborations; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.collaborations (id, internal_id, name, date_updated) FROM stdin;
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
-- Data for Name: term_relationships; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.term_relationships (source_term_id, related_term_id) FROM stdin;
\.


--
-- Data for Name: terms; Type: TABLE DATA; Schema: public; Owner: test
--

COPY public.terms (id, term, definition, definition_es, is_abbr, contexts, related_documentation, date_updated) FROM stdin;
\.


--
-- Name: affiliations_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.affiliations_id_seq', 1, false);


--
-- Name: authors_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.authors_id_seq', 1, false);


--
-- Name: collaborations_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.collaborations_id_seq', 1, false);


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
-- Name: terms_id_seq; Type: SEQUENCE SET; Schema: public; Owner: test
--

SELECT pg_catalog.setval('public.terms_id_seq', 1, false);


--
-- Name: affiliations affiliations_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.affiliations
    ADD CONSTRAINT affiliations_pkey PRIMARY KEY (id);


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
-- Name: authors authors_orcid_key; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_orcid_key UNIQUE (orcid);


--
-- Name: authors authors_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_pkey PRIMARY KEY (id);


--
-- Name: collaborations collaborations_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.collaborations
    ADD CONSTRAINT collaborations_pkey PRIMARY KEY (id);


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
-- Name: term_relationships term_relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term_relationships
    ADD CONSTRAINT term_relationships_pkey PRIMARY KEY (source_term_id, related_term_id);


--
-- Name: terms terms_pkey; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.terms
    ADD CONSTRAINT terms_pkey PRIMARY KEY (id);


--
-- Name: affiliations uq_affiliation_internal_id_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.affiliations
    ADD CONSTRAINT uq_affiliation_internal_id_name UNIQUE (internal_id, name);


--
-- Name: collaborations uq_collaboration_internal_id_name; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.collaborations
    ADD CONSTRAINT uq_collaboration_internal_id_name UNIQUE (internal_id, name);


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
-- Name: terms uq_term_definition; Type: CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.terms
    ADD CONSTRAINT uq_term_definition UNIQUE (term, definition);


--
-- Name: ix_affiliations_address; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_affiliations_address ON public.affiliations USING btree (address);


--
-- Name: ix_affiliations_internal_id; Type: INDEX; Schema: public; Owner: test
--

CREATE UNIQUE INDEX ix_affiliations_internal_id ON public.affiliations USING btree (internal_id);


--
-- Name: ix_affiliations_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_affiliations_name ON public.affiliations USING btree (name);


--
-- Name: ix_authors_given_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_authors_given_name ON public.authors USING btree (given_name);


--
-- Name: ix_authors_internal_id; Type: INDEX; Schema: public; Owner: test
--

CREATE UNIQUE INDEX ix_authors_internal_id ON public.authors USING btree (internal_id);


--
-- Name: ix_authors_surname; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_authors_surname ON public.authors USING btree (surname);


--
-- Name: ix_collaborations_internal_id; Type: INDEX; Schema: public; Owner: test
--

CREATE UNIQUE INDEX ix_collaborations_internal_id ON public.collaborations USING btree (internal_id);


--
-- Name: ix_collaborations_name; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_collaborations_name ON public.collaborations USING btree (name);


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
-- Name: ix_terms_contexts; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_terms_contexts ON public.terms USING btree (contexts);


--
-- Name: ix_terms_definition; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_terms_definition ON public.terms USING btree (definition);


--
-- Name: ix_terms_term; Type: INDEX; Schema: public; Owner: test
--

CREATE INDEX ix_terms_term ON public.terms USING btree (term);


--
-- Name: author_affiliations author_affiliations_affiliation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author_affiliations
    ADD CONSTRAINT author_affiliations_affiliation_id_fkey FOREIGN KEY (affiliation_id) REFERENCES public.affiliations(id);


--
-- Name: author_affiliations author_affiliations_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.author_affiliations
    ADD CONSTRAINT author_affiliations_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.authors(id);


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
-- Name: term_relationships term_relationships_related_term_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term_relationships
    ADD CONSTRAINT term_relationships_related_term_id_fkey FOREIGN KEY (related_term_id) REFERENCES public.terms(id) ON DELETE CASCADE;


--
-- Name: term_relationships term_relationships_source_term_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: test
--

ALTER TABLE ONLY public.term_relationships
    ADD CONSTRAINT term_relationships_source_term_id_fkey FOREIGN KEY (source_term_id) REFERENCES public.terms(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

