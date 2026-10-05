"""Evaluate fixed and causal adaptive ensembles against the production VAR v2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml.backtest_var import cycle_accuracy
from pulso_transmi.submit_current_cycle import multivariate_autoregressive_predictions, weighted_median


def adaptive_prediction(predictions, actual, origins, index, mode):
    """Only score forecasts whose entire target window was observable at inference."""
    baseline=predictions[0,index]
    available=origins[index]-pd.Timedelta(minutes=30)
    known=np.array([o+pd.Timedelta(hours=1)<=available and o>=available-pd.Timedelta(hours=8) for o in origins])
    if known.sum()<4:
        return baseline.copy()
    previous=predictions[:,known]
    truth=actual[known]
    if mode == "gated":
        # Pool horizons to avoid selecting a station strategy from four samples.
        valid=np.isfinite(truth)
        error=np.nansum(np.abs(previous-truth[None]),axis=(1,2))
        counts=valid.sum(axis=(0,1))
        use=(counts>=16)&(error[1]<.85*error[0])
        return np.where(use[None],.5*baseline+.5*predictions[1,index],baseline)
    if mode == "calibrated":
        output=baseline.copy()
        for h in range(4):
            for s in range(12):
                p=previous[0,:,h,s]; y=truth[:,h,s]
                valid=np.isfinite(y)&np.isfinite(p)&(p>0)
                if valid.sum()>=4:
                    factor=float(np.clip(weighted_median(y[valid]/p[valid],p[valid]),.85,1.15))
                    output[h,s]*=factor
        return output
    error=np.nansum(np.abs(previous-truth[None]),axis=1)
    totals=np.nansum(truth,axis=0)
    loss=np.divide(error,totals[None],out=np.ones_like(error),where=totals[None]>0)
    weights=np.exp(-np.minimum(loss,10)/.15)
    weights/=weights.sum(axis=0,keepdims=True)
    blend=(predictions[:,index]*weights).sum(axis=0)
    return .5*baseline+.5*blend


def summarize(values):
    return {"cycles":len(values),"mean":float(np.mean(values)),"minimum":float(min(values)),
            "cycles_below_70":sum(v<70 for v in values),"cycles_below_80":sum(v<80 for v in values)}


def harmonic_prediction(pivot, origins, window, periods):
    output=[]
    for origin in origins:
        available=origin-pd.Timedelta(minutes=30)
        past=pivot.loc[:available].tail(window)
        offsets=(past.index-available).total_seconds().to_numpy()/900
        future=np.arange(3,7,dtype=float)
        def design(x):
            return np.column_stack([np.ones(len(x))]+[f(2*np.pi*x/p) for p in periods for f in (np.sin,np.cos)])
        x=design(offsets); xf=design(future); predicted=[]
        for station in pivot.columns:
            y=past[station].to_numpy(float); valid=np.isfinite(y)
            if valid.sum()<x.shape[1]*2:
                predicted.append(np.full(4,float(y[valid][-1]) if valid.any() else 0))
                continue
            penalty=np.eye(x.shape[1])*.1; penalty[0,0]=0
            coef=np.linalg.solve(x[valid].T@x[valid]+penalty,x[valid].T@y[valid])
            predicted.append(np.maximum(0,xf@coef))
        output.append(np.stack(predicted,axis=1))
    return np.asarray(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path)
    parser.add_argument("--report",type=Path,default=Path("artifacts/var-ensemble-evaluation.json"))
    parser.add_argument("--hours",type=int,default=72)
    parser.add_argument("--holdout-hours",type=int,default=24)
    args=parser.parse_args()
    if not 0<args.holdout_hours<args.hours:
        parser.error("Invalid chronological split")
    if args.input:
        raw=pd.read_csv(args.input,dtype={"station_id":"string"})
    else:
        from pulso_transmi.model_tournament import load_observations
        from pulso_transmi.submit_current_cycle import load_env
        load_env()
        raw=load_observations()
    raw["observed_at"]=pd.to_datetime(raw.observed_at,utc=True)
    pivot=raw.pivot(index="observed_at",columns="station_id",values="demand").sort_index()
    stations=list(pivot.columns)
    if len(stations)!=12:
        raise RuntimeError("Twelve stations required")
    end=raw.observed_at.max().floor("h")-pd.Timedelta(hours=1)
    origins=pd.date_range(end-pd.Timedelta(hours=args.hours-1),end,freq="h")
    split=end-pd.Timedelta(hours=args.holdout_hours)
    raw=raw[raw.observed_at>=origins[0]-pd.Timedelta(days=2)]
    actual=np.array([pivot.reindex(pd.date_range(o+pd.Timedelta(minutes=15),periods=4,freq="15min")).to_numpy(float) for o in origins])
    rows=[]
    for o in origins:
        available=o-pd.Timedelta(minutes=30)
        latest=raw[raw.observed_at<=available].sort_values("observed_at").groupby("station_id",observed=True).tail(2)
        lags={s:list(g.demand) for s,g in latest.groupby("station_id",observed=True)}
        for h in range(1,5):
            for s in stations:
                rows.append({"station_id":s,"target_at":o+pd.Timedelta(minutes=15*h),"horizon_steps":h,
                             "lag_available":float(lags[s][-1]),"lag_15m":float(lags[s][0])})
    frame=pd.DataFrame(rows)
    configs={"var-v2":(3,32,1.0),"weak-ridge":(3,32,.1),"long-4":(4,48,1.0),"short-3":(3,16,1.0)}
    predictions={}
    for name,params in configs.items():
        predictions[name]=multivariate_autoregressive_predictions(raw,frame,*params).reshape(len(origins),4,12)
        print("Evaluated",name,flush=True)
    for window in (16,32,48):
        parts=[]
        for s in stations:
            subset=frame[frame.station_id==s]
            parts.append(multivariate_autoregressive_predictions(raw[raw.station_id==s],subset,3,window,1.0).reshape(len(origins),4))
        predictions[f"local-{window}"]=np.stack(parts,axis=2)
    predictions["blend-weak"]=.5*predictions["var-v2"]+.5*predictions["weak-ridge"]
    predictions["blend-long"]=.5*predictions["var-v2"]+.5*predictions["long-4"]
    predictions["blend-local"]=.5*predictions["var-v2"]+.5*predictions["local-32"]
    for window in (32,64,96):
        for periods in ((16,),(16,32),(16,32,96)):
            name="harmonic-"+str(window)+"-"+"-".join(map(str,periods))
            predictions[name]=harmonic_prediction(pivot,origins,window,periods)
            predictions["blend-"+name]=.5*predictions["var-v2"]+.5*predictions[name]
    bank=np.stack([predictions[k] for k in ("var-v2","weak-ridge","long-4","local-32")])
    for mode in ("calibrated","adaptive"):
        predictions[mode]=np.array([adaptive_prediction(bank,actual,origins,i,mode) for i in range(len(origins))])
    for complement in ("weak-ridge","long-4","harmonic-32-16-32-96"):
        gated_bank=np.stack([predictions["var-v2"],predictions[complement]])
        predictions["gated-"+complement]=np.array([adaptive_prediction(gated_bank,actual,origins,i,"gated") for i in range(len(origins))])
    results={}
    skipped=[o.isoformat() for o,a in zip(origins,actual) if not np.isfinite(a).all()]
    for name,predicted in predictions.items():
        cycles=[{"origin":o.isoformat(),"partition":"holdout" if o>split else "tuning","accuracy":cycle_accuracy(a,p)}
                for o,a,p in zip(origins,actual,predicted) if np.isfinite(a).all()]
        results[name]={"cycles":cycles}
        for partition in ("tuning","holdout"):
            values=[c["accuracy"] for c in cycles if c["partition"]==partition]
            if not values:
                raise RuntimeError("No complete cycles; cannot promote")
            results[name][partition]=summarize(values)
    def objective(name):
        r=results[name]["tuning"]
        return r["mean"]+.1*r["minimum"]-10*r["cycles_below_70"]/r["cycles"]
    selected=max(results,key=objective)
    candidate=results[selected]["holdout"]; base=results["var-v2"]["holdout"]
    eligible=(selected!="var-v2" and candidate["cycles"]>=12 and candidate["mean"]>=max(80.0,base["mean"]+.5)
              and candidate["minimum"]>=base["minimum"] and candidate["cycles_below_70"]<=base["cycles_below_70"])
    report={"data_end":raw.observed_at.max().isoformat(),"split":split.isoformat(),"selected_on_tuning":selected,
            "eligible_for_review":eligible,"skipped_incomplete_cycles":skipped,"results":results,
            "production_changed":False,"scope":"Causal hourly simulations; not official leaderboard",
            "promotion_policy":"Select on tuning; >=12 complete holdout cycles, mean>=80 and +0.5pp, no worse minimum or count below70",
            "limitation":"Repeated inspection of holdout is exploratory; deployment requires independent subsequent evaluation"}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n")
    print("Selected",selected,"eligible",eligible)
    for k,v in results.items():
        print(k,"tuning",round(v["tuning"]["mean"],2),"holdout",v["holdout"])


if __name__=="__main__":
    main()
