# 能力规格 / Capability specifications

契约以各目录 JSON 和锁定 manifest 为准。此表不表示已完成 live App 验收。


## capture

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `capture.create` | internal_write | standalone | `content`, `source` |
| `capture.get` | read_only | standalone | `id` |
| `capture.list` | read_only | standalone |  |
| `capture.archive` | internal_write | standalone | `id`, `expected_version` |
| `capture.restore` | internal_write | standalone | `id`, `expected_version` |
| `capture.route` | internal_write | standalone | `id`, `expected_version`, `classification` |
| `capture.mark_processed` | internal_write | standalone | `id`, `expected_version` |


## idea

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `idea.create` | internal_write | standalone | `title` |
| `idea.get` | read_only | standalone | `id` |
| `idea.list` | read_only | standalone |  |
| `idea.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `idea.discard` | internal_write | standalone | `id`, `expected_version` |
| `idea.delete` | internal_write | standalone | `id`, `expected_version` |
| `idea.archive` | internal_write | standalone | `id`, `expected_version` |
| `idea.restore` | internal_write | standalone | `id`, `expected_version` |
| `idea.convert` | internal_write | standalone | `id`, `expected_version`, `target_ref` |


## bug

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `bug.create` | internal_write | standalone | `title` |
| `bug.get` | read_only | standalone | `id` |
| `bug.list` | read_only | standalone |  |
| `bug.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `bug.resolve` | internal_write | standalone | `id`, `expected_version` |
| `bug.reopen` | internal_write | standalone | `id`, `expected_version` |
| `bug.close` | internal_write | standalone | `id`, `expected_version` |
| `bug.delete` | internal_write | standalone | `id`, `expected_version` |
| `bug.archive` | internal_write | standalone | `id`, `expected_version` |
| `bug.restore` | internal_write | standalone | `id`, `expected_version` |
| `bug.link_task` | internal_write | standalone | `id`, `expected_version`, `target_ref` |


## finance

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `finance.statement.create` | internal_write | standalone | `month`, `currency`, `source_id`, `transactions` |
| `finance.statement.get` | read_only | standalone | `id` |
| `finance.statement.list` | read_only | standalone |  |
| `finance.monthly_snapshot.get` | read_only | standalone | `id` |
| `finance.monthly_snapshot.list` | read_only | standalone |  |
| `finance.monthly_snapshot.create` | internal_write | standalone | `month`, `currency`, `source_id`, `income`, `expense`, `assets`, `liabilities` |
| `finance.monthly_snapshot.close` | internal_write | standalone | `id`, `expected_version` |
| `finance.summary` | read_only | standalone | `month`, `currency` |


## relationship

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `relationship.person.create` | internal_write | standalone | `name` |
| `relationship.person.get` | read_only | standalone | `id` |
| `relationship.person.list` | read_only | standalone |  |
| `relationship.person.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `relationship.person.archive` | internal_write | standalone | `id`, `expected_version` |
| `relationship.person.restore` | internal_write | standalone | `id`, `expected_version` |
| `relationship.interaction.record` | internal_write | standalone | `person_id`, `at` |
| `relationship.interaction.list` | read_only | standalone |  |
| `relationship.recent` | read_only | standalone | `person_id` |


## habit

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `habit.create` | internal_write | standalone | `name`, `timezone` |
| `habit.get` | read_only | standalone | `id` |
| `habit.list` | read_only | standalone |  |
| `habit.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `habit.pause` | internal_write | standalone | `id`, `expected_version` |
| `habit.resume` | internal_write | standalone | `id`, `expected_version` |
| `habit.delete` | internal_write | standalone | `id`, `expected_version` |
| `habit.archive` | internal_write | standalone | `id`, `expected_version` |
| `habit.restore` | internal_write | standalone | `id`, `expected_version` |
| `habit.checkin` | internal_write | standalone | `habit_id`, `date` |
| `habit.undo_checkin` | internal_write | standalone | `habit_id`, `date` |
| `habit.history` | read_only | standalone | `habit_id` |


## body

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `body.state.record` | internal_write | standalone | `at`, `source` |
| `body.state.get` | read_only | standalone | `id` |
| `body.state.get_latest` | read_only | standalone |  |
| `body.state.history` | read_only | standalone |  |
| `body.sleep.record` | internal_write | standalone | `at`, `minutes`, `source` |
| `body.training.record` | internal_write | standalone | `at`, `minutes`, `activity`, `source` |
| `body.recovery.summary` | read_only | standalone |  |


## lifeadmin

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `lifeadmin.create` | internal_write | standalone | `title`, `due_date` |
| `lifeadmin.get` | read_only | standalone | `id` |
| `lifeadmin.list` | read_only | standalone |  |
| `lifeadmin.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `lifeadmin.complete` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.delete` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.archive` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.restore` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.renew` | internal_write | standalone | `id`, `expected_version`, `due_date`, `reason` |


## decision

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `decision.create` | internal_write | standalone | `title`, `options` |
| `decision.get` | read_only | standalone | `id` |
| `decision.list` | read_only | standalone |  |
| `decision.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `decision.delete` | internal_write | standalone | `id`, `expected_version` |
| `decision.archive` | internal_write | standalone | `id`, `expected_version` |
| `decision.restore` | internal_write | standalone | `id`, `expected_version` |
| `decision.choose` | internal_write | standalone | `id`, `expected_version`, `option_id`, `reason` |
| `decision.outcome` | internal_write | standalone | `id`, `expected_version`, `record_id`, `at`, `outcome` |
| `decision.review` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |


## planner

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `planner.plan.create` | internal_write | standalone | `title`, `tasks`, `windows` |
| `planner.plan.get` | read_only | standalone | `id` |
| `planner.plan.list` | read_only | standalone |  |
| `planner.plan.archive` | internal_write | standalone | `id`, `expected_version` |
| `planner.plan.restore` | internal_write | standalone | `id`, `expected_version` |
| `planner.generate` | internal_write | standalone | `tasks`, `windows` |
| `planner.preview` | read_only | standalone | `tasks`, `windows` |
| `planner.suggest` | read_only | standalone | `tasks`, `windows` |
| `planner.apply` | internal_write | standalone | `id`, `expected_version`, `input_digest` |
| `planner.replan` | internal_write | standalone | `id`, `expected_version`, `inputs` |


## deepwork

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `deepwork.start` | internal_write | standalone | `task_ref`, `at` |
| `deepwork.get` | read_only | standalone | `id` |
| `deepwork.list` | read_only | standalone |  |
| `deepwork.pause` | internal_write | standalone | `id`, `expected_version`, `at` |
| `deepwork.resume` | internal_write | standalone | `id`, `expected_version`, `at` |
| `deepwork.finish` | internal_write | standalone | `id`, `expected_version`, `at` |
| `deepwork.cancel` | internal_write | standalone | `id`, `expected_version`, `at` |
| `deepwork.interrupt` | internal_write | standalone | `id`, `expected_version`, `at`, `reason` |


## competition

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `competition.create` | internal_write | standalone | `title` |
| `competition.get` | read_only | standalone | `id` |
| `competition.list` | read_only | standalone |  |
| `competition.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `competition.delete` | internal_write | standalone | `id`, `expected_version` |
| `competition.archive` | internal_write | standalone | `id`, `expected_version` |
| `competition.restore` | internal_write | standalone | `id`, `expected_version` |
| `competition.milestone` | internal_write | standalone | `id`, `expected_version`, `record_id`, `at`, `label`, `value` |
| `competition.progress` | internal_write | standalone | `id`, `expected_version`, `record_id`, `at`, `label`, `value` |
| `competition.review` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |


## knowledge

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `knowledge.create` | internal_write | standalone | `title` |
| `knowledge.get` | read_only | standalone | `id` |
| `knowledge.list` | read_only | standalone |  |
| `knowledge.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `knowledge.delete` | internal_write | standalone | `id`, `expected_version` |
| `knowledge.archive` | internal_write | standalone | `id`, `expected_version` |
| `knowledge.restore` | internal_write | standalone | `id`, `expected_version` |
| `knowledge.concept.record` | internal_write | standalone | `knowledge_id`, `label` |
| `knowledge.link.record` | internal_write | standalone | `knowledge_id`, `target_ref`, `relation` |
| `knowledge.insight.record` | internal_write | standalone | `knowledge_id`, `statement`, `evidence_refs` |
| `knowledge.search` | read_only | standalone | `query` |


## creation

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `creation.create` | internal_write | standalone | `title` |
| `creation.get` | read_only | standalone | `id` |
| `creation.list` | read_only | standalone |  |
| `creation.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `creation.delete` | internal_write | standalone | `id`, `expected_version` |
| `creation.archive` | internal_write | standalone | `id`, `expected_version` |
| `creation.restore` | internal_write | standalone | `id`, `expected_version` |
| `creation.advance_stage` | internal_write | standalone | `id`, `expected_version`, `stage` |
| `creation.publication` | internal_write | standalone | `id`, `expected_version`, `url`, `at` |


## review

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `review.generate` | internal_write | standalone | `period_start`, `period_end`, `sources`, `missing_sources` |
| `review.get` | read_only | standalone | `id` |
| `review.list` | read_only | standalone |  |
| `review.finalize` | internal_write | standalone | `id`, `expected_version` |
| `review.compare` | read_only | standalone | `id`, `other_id` |
| `review.interpret` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |


## personal_model

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `personal-model.hypothesis.create` | internal_write | standalone | `statement`, `source` |
| `personal-model.hypothesis.get` | read_only | standalone | `id` |
| `personal-model.hypothesis.list` | read_only | standalone |  |
| `personal-model.hypothesis.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `personal-model.hypothesis.withdraw` | internal_write | standalone | `id`, `expected_version` |
| `personal-model.hypothesis.archive` | internal_write | standalone | `id`, `expected_version` |
| `personal-model.hypothesis.restore` | internal_write | standalone | `id`, `expected_version` |
| `personal-model.attach` | internal_write | standalone | `id`, `expected_version`, `reference` |
| `personal-model.evaluate` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs`, `confidence` |


## cognition

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `cognition.pattern.create` | internal_write | standalone | `statement`, `source` |
| `cognition.pattern.get` | read_only | standalone | `id` |
| `cognition.pattern.list` | read_only | standalone |  |
| `cognition.pattern.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `cognition.pattern.withdraw` | internal_write | standalone | `id`, `expected_version` |
| `cognition.pattern.archive` | internal_write | standalone | `id`, `expected_version` |
| `cognition.pattern.restore` | internal_write | standalone | `id`, `expected_version` |
| `cognition.analyze` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |
| `cognition.pattern.review` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |
| `cognition.pattern.changes` | read_only | standalone |  |


## task

| Capability | Effect | Mode |
|---|---|---|

| `task.create` | internal_write | standalone |

| `task.get` | read_only | standalone |

| `task.list` | read_only | standalone |

| `task.update` | internal_write | standalone |

| `task.complete` | internal_write | standalone |

| `task.reopen` | internal_write | standalone |

| `task.delete` | internal_write | standalone |

| `task.cancel` | internal_write | standalone |

| `task.archive` | internal_write | standalone |


## feishu

| Capability | Effect | Mode |
|---|---|---|

| `feishu.task.create` | external_write | standalone |

| `feishu.task.get` | read_only | standalone |

| `feishu.task.list` | read_only | standalone |

| `feishu.task.update` | external_write | standalone |

| `feishu.task.complete` | external_write | standalone |

| `feishu.calendar.event.create` | external_write | standalone |

| `feishu.calendar.event.get` | read_only | standalone |

| `feishu.calendar.event.list` | read_only | standalone |

| `feishu.calendar.event.update` | external_write | standalone |

| `feishu.message.send` | external_write | standalone |

| `feishu.resource.bindings.get` | read_only | standalone |


## ima

| Capability | Effect | Mode |
|---|---|---|

| `ima.note.create` | external_write | standalone |

| `ima.note.append` | external_write | standalone |

| `ima.note.get` | read_only | standalone |

| `ima.note.list` | read_only | standalone |

| `ima.note.search` | read_only | standalone |

| `ima.notebook.list` | read_only | standalone |

| `ima.kb.get` | read_only | standalone |

| `ima.kb.list` | read_only | standalone |

| `ima.kb.search` | read_only | standalone |

| `ima.knowledge.search` | read_only | standalone |

| `ima.note.delete` | external_write | standalone |

| `ima.note.overwrite` | external_write | standalone |

| `ima.kb.associate` | external_write | standalone |
