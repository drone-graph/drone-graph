SCHEMA_STATEMENTS: list[str] = [
    "CREATE CONSTRAINT gap_id_unique IF NOT EXISTS FOR (g:Gap) REQUIRE g.id IS UNIQUE",
    "CREATE CONSTRAINT finding_id_unique IF NOT EXISTS FOR (f:Finding) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT drone_id_unique IF NOT EXISTS FOR (d:Drone) REQUIRE d.id IS UNIQUE",
    "CREATE INDEX gap_status IF NOT EXISTS FOR (g:Gap) ON (g.status)",
    "CREATE INDEX gap_created_at IF NOT EXISTS FOR (g:Gap) ON (g.created_at)",
]
