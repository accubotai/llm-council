import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import './ChatInterface.css';

export default function ChatInterface({
  conversation,
  onSendMessage,
  onFollowUp,
  followUpModel,
  onSelectFollowUpModel,
  isLoading,
}) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const shouldAutoScroll = useRef(true);

  const scrollToBottom = () => {
    if (shouldAutoScroll.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const handleScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const threshold = 100;
    shouldAutoScroll.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  };

  useEffect(() => {
    scrollToBottom();
  }, [conversation]);

  // Check if council has completed (has a stage3 result in any assistant message)
  const councilComplete = conversation?.messages?.some(
    (msg) => msg.role === 'assistant' && msg.stage3 && !msg.stage3.streaming
  );

  // Get available models from stage1 responses
  const availableModels = [];
  if (conversation?.messages) {
    for (const msg of conversation.messages) {
      if (msg.role === 'assistant' && msg.stage1) {
        for (const r of msg.stage1) {
          if (r.model && r.response && !availableModels.includes(r.model)) {
            availableModels.push(r.model);
          }
        }
      }
      // Also include chairman
      if (msg.role === 'assistant' && msg.stage3?.model && !availableModels.includes(msg.stage3.model)) {
        availableModels.push(msg.stage3.model);
      }
    }
  }

  const isCouncilMode = followUpModel === '__council__';
  const isFollowUpMode = councilComplete && followUpModel && !isCouncilMode;
  const showInput = conversation && (conversation.messages.length === 0 || isFollowUpMode || isCouncilMode);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      if (isCouncilMode) {
        onSendMessage(input);
      } else if (isFollowUpMode) {
        onFollowUp(input);
      } else {
        onSendMessage(input);
      }
      setInput('');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-interface">
      <div className="messages-container" ref={messagesContainerRef} onScroll={handleScroll}>
        {conversation.messages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
          </div>
        ) : (
          conversation.messages.map((msg, index) => (
            <div key={index} className="message-group">
              {msg.role === 'user' ? (
                <div className="user-message">
                  <div className="message-label">You</div>
                  <div className="message-content">
                    <div className="markdown-content">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              ) : msg.followup ? (
                <div className="followup-message">
                  <div className="message-label">
                    {msg.model?.split('/')[1] || msg.model || 'Assistant'}
                  </div>
                  <div className="followup-content markdown-content">
                    {msg.content ? (
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    ) : (
                      <span className="waiting-text">Thinking...</span>
                    )}
                    {msg.streaming && <span className="cursor-blink">|</span>}
                  </div>
                </div>
              ) : (
                <div className="assistant-message">
                  <div className="message-label">LLM Council</div>

                  {msg.loading?.stage1 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 1: Collecting individual responses...</span>
                    </div>
                  )}
                  {msg.stage1 && <Stage1 responses={msg.stage1} />}

                  {msg.loading?.stage2 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 2: Peer rankings...</span>
                    </div>
                  )}
                  {msg.stage2 && (
                    <Stage2
                      rankings={msg.stage2}
                      labelToModel={msg.metadata?.label_to_model}
                      aggregateRankings={msg.metadata?.aggregate_rankings}
                    />
                  )}

                  {msg.loading?.stage3 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 3: Final synthesis...</span>
                    </div>
                  )}
                  {msg.stage3 && <Stage3 finalResponse={msg.stage3} />}
                </div>
              )}
            </div>
          ))
        )}

        {isLoading && !isFollowUpMode && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Consulting the council...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Model selector - shown after council completes */}
      {councilComplete && availableModels.length > 0 && (
        <div className="followup-bar">
          <span className="followup-label">Continue with:</span>
          <div className="model-selector">
            <button
              className={`model-btn council-btn ${followUpModel === '__council__' ? 'active' : ''}`}
              onClick={() => onSelectFollowUpModel(followUpModel === '__council__' ? null : '__council__')}
            >
              LLM Council
            </button>
            {availableModels.map((m) => (
              <button
                key={m}
                className={`model-btn ${followUpModel === m ? 'active' : ''}`}
                onClick={() => onSelectFollowUpModel(followUpModel === m ? null : m)}
              >
                {m.split('/')[1] || m}
              </button>
            ))}
          </div>
        </div>
      )}

      {showInput && (
        <form className="input-form" onSubmit={handleSubmit}>
          <textarea
            className="message-input"
            placeholder={
              isCouncilMode
                ? 'Ask the council a follow-up... (Shift+Enter for new line, Enter to send)'
                : isFollowUpMode
                ? `Ask ${followUpModel?.split('/')[1] || followUpModel}... (Shift+Enter for new line)`
                : 'Ask your question... (Shift+Enter for new line, Enter to send)'
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            rows={3}
          />
          <button
            type="submit"
            className="send-button"
            disabled={!input.trim() || isLoading}
          >
            Send
          </button>
        </form>
      )}
    </div>
  );
}
