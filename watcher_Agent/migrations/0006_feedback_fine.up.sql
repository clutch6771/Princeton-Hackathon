-- Match the PRD and Guardian feedback vocabulary; retain historical rerate rows.
ALTER TABLE judge.feedback DROP CONSTRAINT feedback_label_check;
ALTER TABLE judge.feedback ADD CONSTRAINT feedback_label_check
    CHECK (label IN ('agree', 'disagree', 'not_for_me', 'fine', 'rerate'));
