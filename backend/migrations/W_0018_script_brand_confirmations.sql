-- Brand confirmations belong to a single decision and its immutable review target.
CREATE TABLE workflow_script_confirmations (
 decision_id TEXT NOT NULL REFERENCES workflow_decisions(id),
 rule_id TEXT NOT NULL,
 PRIMARY KEY(decision_id, rule_id)
);
