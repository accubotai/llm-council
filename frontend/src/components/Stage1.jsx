import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage1.css';

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!responses || responses.length === 0) {
    return null;
  }

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${resp.streaming ? 'streaming' : ''}`}
            onClick={() => setActiveTab(index)}
          >
            {resp.streaming && <span className="streaming-dot" />}
            {resp.model.split('/')[1] || resp.model}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="model-name">{responses[activeTab].model}</div>
        <div className="response-text markdown-content">
          {responses[activeTab].response ? (
            <ReactMarkdown>{responses[activeTab].response}</ReactMarkdown>
          ) : (
            <span className="waiting-text">Waiting for response...</span>
          )}
          {responses[activeTab].streaming && <span className="cursor-blink">|</span>}
        </div>
      </div>
    </div>
  );
}
