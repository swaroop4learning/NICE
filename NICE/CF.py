import pandas as pd
import numpy as np
from NICE.utils.distance import VDM_pairwise_distance,HVDM,HEOM,abdm_alibi,mvdm_alibi,pw_to_distance
from NICE.utils.discretizer import Discretizer
from NICE.utils.preprocessing import OHE_minmax
from NICE.utils.AE import AE_model
from math import ceil
class NICE:
    def __init__(self,verbose = True,justified_cf = True,optimization = 'None',start ='NN'):
        self.start = start
        self.optimization = optimization
        self.verbose = verbose
        self.justified_cf = justified_cf
        self.eps = 0.00001
        #todo use weight 1 for cat_feat and integrate weights into calculation.
        #todo check if all inputs are correct. elevate error
    def fit(self,
            X_train,
            y_train,
            predict_fn,#todo predict_fn should return a label not a prob
            cat_feat,
            feature_names,
            con_feat = 'auto',
            distance_metric='HEOM',
            weights = None,
            percentiles_discretization = (10,20,30,40,50,60,70,80,90)):#todo specify type for each feature
        self.distance_metric = distance_metric
        self.X_train = X_train.astype(np.float64)
        self.y_train = y_train.astype(np.float64)#todo clean up what's supposed to be internal variable
        self.cat_feat = cat_feat
        self.predict_fn = predict_fn
        self.con_feat = con_feat
        self.weights = weights
        self.feature_names = feature_names
        self.percentiles_discretization = percentiles_discretization
        #todo raise error when wrong options are selected
        if self.optimization == 'plausibility':
            self._train_AE(self.X_train)
        if self.weights == None:
            self.weights = np.ones(self.X_train.shape[1])

        if self.con_feat == 'auto':
            self.con_feat = [feat for feat in range(self.X_train.shape[1]) if feat not in self.cat_feat]

        if self.distance_metric in ['MVDM','ABDM']:
            self.disc = Discretizer(self.X_train, self.con_feat, self.feature_names, self.percentiles_discretization)
            self.X_train_discrete = self.disc.discretize(self.X_train)
            self.cat_vars_ord = dict()#todo take this style as input for cat_feat
            for i in self.cat_feat+self.con_feat:
                self.cat_vars_ord[i]=int(np.max(self.X_train_discrete[:,i])+1)

        if self.distance_metric == 'HVDM':
            self.cat_distance,self.con_distance = self._fit_HVDM(self.X_train, self.y_train, cat_feat=self.cat_feat, con_feat=self.con_feat)#todo hardcode self._fit_HVDM here
        elif self.distance_metric == 'HEOM':
            self.con_range = self.X_train[:,self.con_feat].max(axis=0)-self.X_train[:,self.con_feat].min(axis=0)
            self.con_range[self.con_range<self.eps]=self.eps
        elif self.distance_metric == 'MVDM':
            self.pw_distance = mvdm_alibi(self.X_train_discrete,self.y_train,self.cat_vars_ord)
        elif self.distance_metric == 'ABDM':
            self.pw_distance = abdm_alibi(self.X_train_discrete,self.cat_vars_ord)
        self.X_train_class = np.argmax(self.predict_fn(self.X_train), axis=1)
        if self.justified_cf:
            mask_justified = (self.X_train_class == self.y_train)
            self.X_train = self.X_train[mask_justified, :]
            self.y_train = self.y_train[mask_justified]
            self.X_train_class = self.X_train_class[mask_justified]
            if self.distance_metric in ['ABDM','MVDM']:
                self.X_train_discrete = self.X_train_discrete[mask_justified, :]


    def explain(self,X,target_class ='other'):#todo target class 'other'
        self.X = X.astype(np.float64)
        self.X_class = np.argmax(self.predict_fn(self.X), axis=1)[0]
        self.target_class = target_class
        if target_class =='other':
            candidate_mask = self.X_train_class!=self.X_class
            if self.X_class ==1:
                self.target_class=0
            else:
                self.target_class=1
        else:
            candidate_mask = self.X_train_class == target_class

        if self.distance_metric in ['ABDM','MVDM']:
            X_discrete = self.disc.discretize(self.X)
            X_candidates_discrete = self.X_train_discrete[candidate_mask,:]
        else:
            X_candidates=self.X_train[candidate_mask,:]


        if self.distance_metric == 'HVDM':
            distance = HVDM(self.X,X_candidates,self.cat_distance,self.con_distance,self.cat_feat,self.con_feat,normalization='N2' )#Todo make normalizatin conditional parameter for .fit method
        elif self.distance_metric == 'HEOM':
            distance = HEOM(self.X,X_candidates,self.cat_feat,self.con_feat,self.con_range)
        elif self.distance_metric in ['ABDM','MVDM']:
            distance = pw_to_distance(X_discrete, X_candidates_discrete, self.pw_distance)

        NN =  self.X_train[candidate_mask,:][distance.argmin(),:][np.newaxis,:]#todo return best when equal
        if self.optimization=='sparsity':
            NN = self._optimize_sparsity(self.X,NN)
        elif self.optimization == 'proximity':
            NN = self._optimize_proximity(self.X,NN)
        elif self.optimization == 'plausibility':
            NN = self._optimize_plausibility(self.X,NN)
        return NN.copy()



    def _fit_HVDM(self,X,y,cat_feat,con_feat):
        cat_distance = {}
        for feat in cat_feat:
            cat_distance[feat]=VDM_pairwise_distance(X[:, feat], y)

        con_distance=np.array([4*np.std(X[:,feat]) for feat in con_feat])
        con_distance[con_distance<self.eps]=self.eps
        return cat_distance,con_distance

    def _optimize_sparsity(self,X,NN):
        stop = False
        if self.start == 'NN':
            while stop == False:
                diff = np.where(abs(X - NN) > self.eps)[1]
                X_prune = np.tile(NN, (len(diff), 1))
                for r, c in enumerate(diff):
                    X_prune[r, c] = X[0, c]
                score_prune = self.predict_fn(X_prune)
                score_diff = score_prune[:,self.target_class] - score_prune[:,self.X_class]
                if score_diff.max() > 0:
                    NN = X_prune[np.argmax(score_diff), :][np.newaxis, :].copy()
                else:
                    stop = True
        elif self.start == 'original':
            while stop == False:
                diff = np.where(abs(X - NN) > self.eps)[1]
                X_prune = np.tile(X, (len(diff), 1))
                for r, c in enumerate(diff):
                    X_prune[r, c] = NN[0, c]
                score_prune = self.predict_fn(X_prune)
                score_diff = score_prune[:, self.target_class] - score_prune[:, self.X_class]
                X = X_prune[np.argmax(score_diff), :][np.newaxis, :]
                if score_diff.max() > 0:
                    NN = X
                    stop = True
        return NN

    def _optimize_proximity(self,X,NN):
        CF_candidate = X.copy()
        if self.distance_metric in ['ABDM', 'MVDM']:
            X_discrete = self.disc.discretize(X)
        X_score = self.predict_fn(X)[:,self.X_class]
        while self.predict_fn(CF_candidate).argmax()==self.X_class:
            diff = np.where(abs(CF_candidate - NN) > self.eps)[1]
            X_prune = np.tile(CF_candidate, (len(diff), 1))
            for r, c in enumerate(diff):
                X_prune[r, c] = NN[0, c]
            score_prune = self.predict_fn(X_prune)
            score_diff = X_score - score_prune[:, self.X_class]

            if self.distance_metric == 'HVDM':
                distance = HVDM(X, X_prune, self.cat_distance, self.con_distance, self.cat_feat, self.con_feat,
                                normalization='N2')  # Todo make normalizatin conditional parameter for .fit method
                distance-= HVDM(X, CF_candidate, self.cat_distance, self.con_distance, self.cat_feat, self.con_feat,
                                normalization='N2')
            elif self.distance_metric == 'HEOM':
                distance = HEOM(X, X_prune, self.cat_feat, self.con_feat, self.con_range)
                distance -= HEOM(X, CF_candidate, self.cat_feat, self.con_feat, self.con_range)
            elif self.distance_metric in ['ABDM', 'MVDM']:
                CF_candidate_discrete = self.disc.discretize(CF_candidate)
                X_prune_discrete = self.disc.discretize(X_prune)
                distance = pw_to_distance(X_discrete, X_prune_discrete, self.pw_distance)
                distance -= pw_to_distance(X_discrete, CF_candidate_discrete, self.pw_distance)
            idx_max = np.argmax(score_diff/(distance+self.eps))
            CF_candidate = X_prune[idx_max, :][np.newaxis, :]#select the instance that has the highest score diff per unit of distance
            X_score = score_prune[idx_max,self.X_class]
        return CF_candidate

    def _train_AE(self,X_train):
        self.PP = OHE_minmax(cat_feat=self.cat_feat,con_feat=self.con_feat)
        self.PP.fit(X_train)
        self.AE = AE_model(self.PP.transform(X_train).shape[1],2)
        self.AE.fit(self.PP.transform(X_train),self.PP.transform(X_train),
                    batch_size= ceil(X_train.shape[0]/10), epochs=20,verbose = 0)

    def _optimize_plausibility(self,X,NN):
        X_score = self.predict_fn(X)[:,self.X_class]
        CF_candidate = X.copy()
        while self.predict_fn(CF_candidate).argmax()==self.X_class:
            diff = np.where(abs(CF_candidate - NN) > self.eps)[1]
            X_prune = np.tile(CF_candidate, (len(diff), 1))
            for r, c in enumerate(diff):
                X_prune[r, c] = NN[0, c]
            score_prune = self.predict_fn(X_prune)
            score_diff = X_score-score_prune[:, self.X_class]

            X_prune_pp = self.PP.transform(X_prune)
            AE_loss_candidates = np.mean((X_prune_pp - self.AE.predict(X_prune_pp))**2,axis = 1)
            X_pp = self.PP.transform(CF_candidate)
            AE_loss_X = np.mean((X_pp - self.AE.predict(X_pp))**2)
            idx_max = np.argmax(score_diff*(AE_loss_X-AE_loss_candidates))
            CF_candidate = X_prune[idx_max, :][np.newaxis, :]#select the instance that has the highest score diff per unit of distance
            X_score = score_prune[idx_max,self.X_class]
        return CF_candidate