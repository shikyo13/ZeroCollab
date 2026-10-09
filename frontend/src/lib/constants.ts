/* Values shared across the workspace. Field limits mirror the server's caps
 * (named alongside), so the browser stops typing where the server would
 * truncate or reject the text. Change both sides together. */

export const LIMITS = {
  taskTitle: 120,          // project_room.py task title
  taskDetails: 3000,       // project_room.py task details
  checkIn: 1600,           // project_room.py task update entry
  reviewFindings: 3000,    // coordination_records.py review findings
  reviewRevision: 240,     // coordination_records.py revision reference
  request: 2400,           // review requests, follow-ups, results, next action
  goal: 2000,              // room_goals.py goal and done criteria
  spec: 64000,             // project_room.py shared spec
  attachmentNote: 1000,    // project_room.py image note
  name: 60,                // agent and project names
  handle: 40,              // agent handle, see HANDLE_PATTERN
  profileText: 16000,      // agent_profiles.py / team_builder.py background
} as const;

/** Agent handle rule from agent_profiles.py and team_builder.py, as an HTML pattern. */
export const HANDLE_PATTERN = '[a-z][a-z0-9-]{0,39}';
/** Handles the server refuses because they address people or groups. */
export const RESERVED_HANDLES = ['all', 'both', 'user', 'system', 'everyone'];

/** The human's identity in tasks, reviews and follow-ups. */
export const USER = 'user';

/** Window events between the legacy chat scripts (static/*.js) and the workspace. */
export const EVENTS = {
  team: 'workspace-team',              // chat.js: open the Team view
  goal: 'workspace-goal',              // chat.js /goal command, detail = argument
  createTask: 'workspace-create-task', // chat.js message menu, detail = {id, text}
  newProject: 'workspace-new-project', // project switcher: open the create form
  openTask: 'workspace-open-task',     // header bell: detail = {task} or {tab: 'coordination'}
} as const;

export const STORAGE_KEYS = {
  boardTab: 'zerocollab-board-tab',
  boardGroup: 'zerocollab-board-group',
  boardSort: 'zerocollab-board-sort',
  boardFeed: 'zerocollab-board-feed',
  welcomeDismissed: 'zerocollab-welcome-dismissed',
} as const;
