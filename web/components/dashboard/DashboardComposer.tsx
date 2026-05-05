export function DashboardComposer() {
  return (
    <div className="compose-work" data-compose-work>
      <button
        type="button"
        className="compose-work-button"
        data-compose-work-toggle
        aria-label="New task or email"
        aria-expanded="false"
        aria-controls="compose-work-panel"
        title="New task or email"
      >
        <span aria-hidden="true">+</span>
      </button>
      <div
        id="compose-work-panel"
        className="compose-work-panel"
        data-compose-work-panel
        aria-label="New task or email"
        hidden
      >
        <div className="compose-work-header">
          <p className="compose-work-title">New work</p>
          <p className="compose-work-subtitle">Task + email</p>
        </div>

        <form className="compose-work-form" data-compose-work-form action="/dashboard">
          <label className="compose-work-field" htmlFor="compose-work-task">
            <span>Task</span>
            <input
              id="compose-work-task"
              name="task"
              type="text"
              required
              placeholder="Send the launch recap to Priya"
            />
          </label>

          <label className="compose-work-field" htmlFor="compose-work-recipient">
            <span>Send to</span>
            <input
              id="compose-work-recipient"
              name="recipient"
              type="email"
              required
              placeholder="priya@company.com"
            />
          </label>

          <label className="compose-work-field" htmlFor="compose-work-message">
            <span>Message</span>
            <textarea
              id="compose-work-message"
              name="message"
              rows={2}
              placeholder="Add the exact note, context, or deadline to send."
            />
          </label>

          <button type="submit" className="compose-work-submit">
            Add task + email
          </button>
        </form>

        <p className="compose-work-status" data-compose-work-status role="status" aria-live="polite" />
        <div className="compose-work-drafts" data-compose-work-drafts aria-label="Recent drafts" />
      </div>
    </div>
  );
}
