import type { LoadingComponentProps } from "../../../types/components";

export default function LoadingComponent(_: LoadingComponentProps): JSX.Element {
  return (
    <div role="status" className="flex flex-col items-center justify-center">
      <svg
        width="200"
        height="90"
        viewBox="0 0 200 90"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <style>
          {`
            .pwc-slash {
              fill: #D04A02;
              animation: pwcPulse 1.4s infinite ease-in-out;
            }
            .pwc-slash-1 { animation-delay: 0s; }
            .pwc-slash-2 { animation-delay: 0.35s; }

            @keyframes pwcPulse {
              0%   { opacity: 0.35; }
              50%  { opacity: 1; }
              100% { opacity: 0.35; }
            }
          `}
        </style>
        {/* bottom-left parallelogram */}
        <polygon
          className="pwc-slash pwc-slash-1"
          points="31,69 97,69 104,50 38,50"
        />
        {/* top-right parallelogram — gap of 2 units from bottom-left */}
        <polygon
          className="pwc-slash pwc-slash-2"
          points="103,48 168,48 175,28 109,28"
        />
      </svg>
    </div>
  );
}
